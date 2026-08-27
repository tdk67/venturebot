"""T2 — Orchestrator hardening: loud failures + per-agent lifecycle events.

Verification points (REWRITE_PLAN.md Part C, T2):
  (a) monkeypatch a sub-agent (via its Runner) to raise → run ends failed;
      a run_failed event with the reason is emitted; the process stays alive
      (no exception escapes run_orchestrator).
  (b) happy path emits agent_started + agent_finished (agent name, model,
      duration) for each of the 7 sub-agents, in order.

These tests are non-live: they fake the ADK Runner so no LLM call is made.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src import events, run_manager
from src.agents import orchestrator as orch
from src.agents.orchestrator import OrchestratorResult


# --- fakes -----------------------------------------------------------------

class Boom(Exception):
    pass


class _FakeModel:
    def __init__(self, name: str):
        self.model = name


class _FakeAgent:
    def __init__(self, name: str, model: str):
        self.name = name
        self.model = _FakeModel(model)


class _Part:
    def __init__(self, text: str):
        self.text = text


class _Content:
    def __init__(self, text: str):
        self.parts = [_Part(text)]


class _Event:
    def __init__(self, text: str, partial: bool = False):
        self.content = _Content(text) if text else None
        self.partial = partial


class _FakeRunner:
    """Yields a single non-partial text event (no LLM call)."""

    def __init__(self, *args, text: str = "output", raise_exc: Exception | None = None, **kwargs):
        self._text = text
        self._raise_exc = raise_exc

    async def run_async(self, *args, **kwargs):
        if self._raise_exc is not None:
            raise self._raise_exc
        yield _Event(self._text)


class _RaisingRunner:
    """A Runner whose run_async raises immediately (simulates a failing sub-agent)."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def run_async(self, *args, **kwargs):
        raise self._exc
        yield  # pragma: no cover  -- makes this an async generator so `async for` awaits it


class _RunnerFactory:
    def __init__(self, exc: Exception):
        self._exc = exc

    def __call__(self, *args, **kwargs):
        return _RaisingRunner(self._exc)


class _Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, event, payload=None):
        self.events.append((event, payload or {}))


_SEVEN = ["Researcher", "Advocate", "Critic", "Creative", "Judge", "PRD Writer", "Security Auditor"]
_MODELS = ["m-researcher", "m-advocate", "m-critic", "m-creative", "m-judge", "m-prd", "m-auditor"]


# --- tests -----------------------------------------------------------------

def test_happy_path_emits_start_finish_for_seven_agents(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(orch, "emit", rec)
    monkeypatch.setattr(orch, "agent_turn", lambda *a, **k: None)
    monkeypatch.setattr(orch, "capture_turn", lambda *a, **k: None)
    monkeypatch.setattr(orch.store, "log", lambda *a, **k: None)

    async def _noop_spawn(session_id, transcript):
        return None

    monkeypatch.setattr(orch, "_spawn_review_fork", _noop_spawn)
    monkeypatch.setattr(orch, "Runner", _FakeRunner)

    run_manager.manager.start("happy-run", deadline_seconds=60)

    async def drive():
        result = OrchestratorResult(idea="a git diff summarizer")
        for name, model in zip(_SEVEN, _MODELS):
            agent = _FakeAgent(name.lower(), model)
            await orch._run_sub_agent(
                agent, "go", result, name, None, "sid", "user", run_id="run-happy",
            )
        return result

    asyncio.run(drive())

    names = [e for e in rec.events if e[0] in ("agent_started", "agent_finished")]
    assert len(names) == 14, f"expected 14 lifecycle events, got {len(names)}: {rec.events}"

    expected = []
    for name in _SEVEN:
        expected.append(("agent_started", name))
        expected.append(("agent_finished", name))
    order = [(e[0], e[1]["agent"]) for e in names]
    assert order == expected, f"order mismatch: {order}"

    model_by_agent = dict(zip(_SEVEN, _MODELS))
    for ev, payload in rec.events:
        if ev == "agent_started":
            assert payload["model"] == model_by_agent[payload["agent"]]
            assert payload["run_id"] == "run-happy"
        elif ev == "agent_finished":
            assert payload["model"] == model_by_agent[payload["agent"]]
            assert "duration" in payload and payload["duration"] >= 0
            assert payload["run_id"] == "run-happy"


def test_sub_agent_raising_ends_run_failed_and_stays_alive(monkeypatch):
    captured = []
    events.register_run_sink("run-fail-1", lambda ev, data: captured.append((ev, data)))
    monkeypatch.setattr(orch, "Runner", _RunnerFactory(Boom("sub-agent kaboom")))
    try:
        result = asyncio.run(
            orch.run_orchestrator("A benign idea", external_run_id="run-fail-1")
        )
    finally:
        events.unregister_run_sink("run-fail-1")

    assert result.status == "failed", f"status={result.status!r}"
    assert "Boom" in result.error and "kaboom" in result.error, result.error

    run_failed = [c for c in captured if c[0] == "run_failed"]
    assert run_failed, f"run_failed not emitted; captured={captured}"
    assert "kaboom" in run_failed[0][1].get("reason", ""), run_failed[0]
    assert run_failed[0][1].get("run_id") == "run-fail-1"


def test_sub_agent_exception_propagates_from_runner(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(orch, "emit", rec)
    monkeypatch.setattr(orch, "agent_turn", lambda *a, **k: None)
    monkeypatch.setattr(orch, "capture_turn", lambda *a, **k: None)
    monkeypatch.setattr(orch.store, "log", lambda *a, **k: None)
    monkeypatch.setattr(orch, "Runner", lambda *a, **k: _FakeRunner(raise_exc=Boom("nope")))

    run_manager.manager.start("boom-run", deadline_seconds=60)

    async def drive():
        result = OrchestratorResult(idea="idea")
        agent = _FakeAgent("researcher", "m-researcher")
        await orch._run_sub_agent(
            agent, "go", result, "Researcher", None, "sid", "user", run_id="run-x",
        )

    with pytest.raises(Boom):
        asyncio.run(drive())

    # started emitted, finished NOT (the turn did not complete successfully)
    assert any(e[0] == "agent_started" for e in rec.events)
    assert not any(e[0] == "agent_finished" for e in rec.events)
