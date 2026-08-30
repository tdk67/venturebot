"""Durable clarify pause/resume  -- state must survive restarts, no timeouts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.agents import orchestrator as orch
from src.agents.orchestrator import (
    OrchestratorResult,
    ClarifyPaused,
    persist_pause,
    get_pause,
    pop_pause,
    any_pending_pause,
    write_pause,
    _build_turn_prompt,
)


def _sample_result() -> OrchestratorResult:
    r = OrchestratorResult(idea="A CLI tool that summarizes git diffs into plain English")
    r.idea_id = "abc123"
    r.turns_used = 2
    r.research_brief = '{"idea_summary": "git diff summarizer"}'
    r.advocate_argument = "advocate text"
    r.verdict = {"verdict": "PROCEED", "scores": {}}
    return r


def test_persist_and_get_pause_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "_pause_dir", lambda: tmp_path)
    r = _sample_result()
    persist_pause(r, "run1")
    p = get_pause("run1")
    assert p is not None
    assert p["idea"] == r.idea
    assert p["idea_id"] == "abc123"
    assert p["turns_used"] == 2
    assert p["research_brief"].startswith("{")
    assert p["question"] is None or isinstance(p["question"], str)


def test_pop_pause_removes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "_pause_dir", lambda: tmp_path)
    persist_pause(_sample_result(), "run2")
    p = pop_pause("run2")
    assert p["idea_id"] == "abc123"
    assert get_pause("run2") is None
    assert pop_pause("run2") is None


def test_any_pending_pause_oldest_first(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "_pause_dir", lambda: tmp_path)
    persist_pause(_sample_result(), "runA")
    write_pause({"run_id": "runB", "asked_at": 1})
    oldest = any_pending_pause()
    assert oldest["run_id"] == "runB"


def test_clarify_tool_raises_paused_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(orch, "_pause_dir", lambda: tmp_path)

    result = _sample_result()
    emitted = {}
    monkeypatch.setattr("src.agents.orchestrator.emit", lambda ev, payload=None: emitted.update(ev=ev))

    class FakeStore:
        def log(self, *a, **k):
            pass
    tools = orch.OrchestratorTools.__new__(orch.OrchestratorTools)
    tools.result = result
    tools.run_id = "runX"

    with pytest.raises(ClarifyPaused) as exc:
        import asyncio
        asyncio.run(tools.clarify("Which language do you target?"))
    assert "language" in exc.value.question
    saved = get_pause("runX")
    assert saved["question"] == "Which language do you target?"
    assert saved["idea_id"] == "abc123"


def test_turn_prompt_includes_answer_and_idea():
    r = _sample_result()
    prompt = _build_turn_prompt(r, turns_used=0, max_turns=10)
    assert "THE IDEA TO EVALUATE" in prompt
    assert "HUMAN ANSWER" not in prompt

    r.clarification_answer = "German and English"
    r.clarification_question = "Which languages?"
    prompt = _build_turn_prompt(r, turns_used=0, max_turns=10)
    assert "HUMAN ANSWER TO YOUR QUESTION" in prompt
    assert "German and English" in prompt
    # answered question no longer shown as pending
    assert "Pending clarification" not in prompt


def test_result_during_clarify_pause_returns_202_not_ready():
    """While a run is paused for clarification (needs_clarification), /result must return
    HTTP 202 (not ready), ensuring the frontend stays on the clarification gate and does
    not prematurely finalize or close the debate."""
    from fastapi.testclient import TestClient
    from src.dashboard import app, STORE, RunRecord

    c = TestClient(app)
    rec = RunRecord(run_id="clarify-probe", idea="Test Idea", api_key="sk-test")
    rec.status = "needs_clarification"
    rec.result = {"verdict": "PARK", "clarification_question": "Should we pivot?"}
    STORE.register(rec)

    r = c.get("/api/debates/clarify-probe/result")
    assert r.status_code == 202, f"Expected 202 Not Ready during clarification pause, got {r.status_code}"

