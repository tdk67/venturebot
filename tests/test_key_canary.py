"""T3 — BYOK plumbing (REWRITE_PLAN.md Part C, T3 / S2 / D1).

Verification points:
  * create-run with a blank/absent api_key -> 400, ALWAYS (D1: no server-key
    fallback exists in any form; the key is REQUIRED per request).
  * the per-request key is passed to the orchestrator's BYOK factory (memory
    only), never resolved from a server-side env when a user key is supplied.
  * the key is DISCARDED from the in-memory run record at run end.
  * a canary key never reaches stdout/stderr, the workspace/state dirs, or the
    per-run result after the run ends.
  * error text that happens to contain the key is REDACTED in run state/events.

These tests are NON-LIVE: they drive the dashboard run-executor and the
orchestrator with fakes so no real LLM call is ever made.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from src import dashboard
from src.dashboard import app, RunRecord, _redact

client = TestClient(app)


# --- D1: key is REQUIRED, blank/absent -> 400 always ----------------------

def test_create_debate_absent_key_400_even_with_server_env(monkeypatch):
    """Even if a server-side provider key exists in env, create-run still 400s
    without a per-request key -- there is NO server-key fallback (D1)."""
    monkeypatch.setenv("GOOGLE_API_KEY", "FAKE-SERVER-KEY-GOOGLE")
    monkeypatch.setenv("OPENROUTER_API_KEY", "FAKE-SERVER-KEY-OPENROUTER")
    r = client.post("/api/debates", json={"idea": "a git diff summarizer"})
    assert r.status_code == 400
    assert "api_key" in r.json()["detail"].lower()
    assert "server-fallback" not in r.json()["detail"].lower()


def test_create_debate_blank_key_400_always():
    r = client.post("/api/debates", json={"idea": "x", "api_key": "   "})
    assert r.status_code == 400
    assert "api_key" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Orchestrator receives the per-request key (not a server fallback).
# ---------------------------------------------------------------------------
class _RaisingRunner:
    """A Runner whose run_async raises immediately (no LLM call)."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def run_async(self, *args, **kwargs):
        raise self._exc
        yield  # pragma: no cover


def test_orchestrator_passes_user_key_to_agents_no_env_fallback(monkeypatch):
    """D1: run_orchestrator must hand the supplied api_key to create_agents and
    must NOT fall back to a server-side env key."""
    import src.agents.orchestrator as orch

    captured: dict = {}
    real = orch.create_agents

    def spy(api_key=None):
        captured["api_key"] = api_key
        return {}  # no real agents needed: the Runner raises before any run

    monkeypatch.setattr(orch, "create_agents", spy)
    monkeypatch.setattr(orch, "Runner", lambda *a, **k: _RaisingRunner(Boom("kaput")))

    canary = f"vb-canary-{uuid.uuid4().hex}"
    result = asyncio.run(
        orch.run_orchestrator("a benign idea", api_key=canary, external_run_id="orchest-canary")
    )
    # The key was handed to the agent factory (memory only), verbatim.
    assert captured.get("api_key") == canary, captured
    # The run was still driven to a FAILED result (Runner raised) -- the key is
    # not the cause, and no exception escaped run_orchestrator.
    assert result.status == "failed"


class Boom(Exception):
    pass


# ---------------------------------------------------------------------------
# Dashboard executor plumbing (T3): key in memory -> orchestrator -> discarded.
# ---------------------------------------------------------------------------
def test_run_debate_passes_key_and_discards_afterwards(tmp_path, monkeypatch, capsys):
    """The executor receives a per-run key, forwards it to the orchestrator only,
    and SCRUBS it from the in-memory record in finally."""
    canary = f"vb-canary-{uuid.uuid4().hex}"
    seen: dict = {}

    async def fake_orchestrator(idea, *, api_key=None, external_run_id=None, **kwargs):
        # simulate the orchestrator capturing the key (memory only)
        seen["api_key"] = api_key
        seen["run_id"] = external_run_id
        return type("Res", (), {
            "status": "done",
            "prd": "PRD with no key",
            "verdict": {"verdict": "PROCEED"},
            "events": [],
            "error": None,
        })()

    monkeypatch.setattr("src.dashboard._orchestrator", fake_orchestrator)

    rec = RunRecord(run_id="exec-canary", idea="an idea", api_key=canary)
    asyncio.run(dashboard._run_debate(rec, canary))

    # forwarded the per-request key, tied to the right run_id
    assert seen["api_key"] == canary
    assert seen["run_id"] == "exec-canary"
    # key discarded at end of run -- never retained/reused (D1)
    assert rec.api_key == ""
    # nothing printed the key
    out = capsys.readouterr().out + capsys.readouterr().err
    assert canary not in out
    # nothing in the on-disk state/workspace dirs
    for f in list((tmp_path / "ws").glob("**/*")) + list((tmp_path / "db").glob("**/*")):
        try:
            assert canary not in f.read_text()
        except OSError:
            pass
    assert rec.status == "done"


def test_run_debate_redacts_key_from_error(monkeypatch, capsys):
    """S2: if the failure message happens to contain the key, it is redacted
    from the run record and from the emitted events."""
    canary = f"vb-canary-{uuid.uuid4().hex}"

    async def boom(idea, *, api_key=None, external_run_id=None, **k):
        raise RuntimeError(f"upstream auth failed: {api_key}")

    monkeypatch.setattr("src.dashboard._orchestrator", boom)
    rec = RunRecord(run_id="err-canary", idea="idea", api_key=canary)
    asyncio.run(dashboard._run_debate(rec, canary))

    assert rec.status == "failed"
    assert canary not in rec.error
    assert "[REDACTED]" in rec.error
    # the emitted event payload is also clean
    for ev in rec.events:
        blob = str(ev)
        assert canary not in blob, blob


def test_redact_helper():
    assert _redact("token abc secret", "abc") == "token [REDACTED] secret"
    assert _redact("nothing to see", "abc") == "nothing to see"
    assert _redact("x", "") == "x"