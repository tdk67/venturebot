"""Tests verifying code review fixes and edge-case hardening."""
from __future__ import annotations

import asyncio
import pytest
from fastapi.testclient import TestClient

from src import config, run_manager
from src.agents import orchestrator as orch
from src.agents.orchestrator import OrchestratorResult, _check_quality_gate, _build_turn_prompt
from src.dashboard import app, _is_intermittent_error, _is_intermittent_error_msg, _build_result_dict, RunRecord
from src.ephemeral_store import EphemeralStore


def test_quality_gate_stops_on_terminal_status():
    res = OrchestratorResult(idea="Test Idea", status="done")
    should_stop, reason = _check_quality_gate(res, turns_used=1, stall_count=0)
    assert should_stop is True
    assert "done" in reason


def test_quality_gate_stops_on_stall_turns():
    res = OrchestratorResult(
        idea="Test Idea",
        prd="# PRD Content",
        verdict={"verdict": "PROCEED", "scores": {"novelty": {"score": 8}, "feasibility": {"score": 8}, "market_fit": {"score": 8}}},
    )
    should_stop, reason = _check_quality_gate(res, turns_used=3, stall_count=config.ORCHESTRATOR_STALL_TURNS)
    assert should_stop is True
    assert "PRD unchanged" in reason


def test_quality_gate_stops_on_max_turns():
    res = OrchestratorResult(
        idea="Test Idea",
        prd="# PRD Content",
        verdict={"verdict": "PROCEED"},
    )
    should_stop, reason = _check_quality_gate(res, turns_used=config.ORCHESTRATOR_MAX_TURNS, stall_count=0)
    assert should_stop is True
    assert "reached max turns" in reason


def test_quality_gate_stops_on_passed_security_audit():
    res = OrchestratorResult(
        idea="Test Idea",
        prd="# PRD Content",
        verdict={"verdict": "PROCEED"},
        security_audit={"ok": True},
    )
    should_stop, reason = _check_quality_gate(res, turns_used=2, stall_count=0)
    assert should_stop is True
    assert "security audit passed" in reason


def test_quality_gate_continues_when_in_progress():
    res = OrchestratorResult(
        idea="Test Idea",
        research_brief="Research findings...",
    )
    should_stop, reason = _check_quality_gate(res, turns_used=1, stall_count=0)
    assert should_stop is False
    assert reason == ""


def test_build_turn_prompt_includes_quarantined_data():
    res = OrchestratorResult(
        idea="A novel logistics tracker",
        clarification_question="What is the scale?",
        clarification_answer="10,000 trucks",
        resume_comment="Focus on EU markets",
    )
    prompt = _build_turn_prompt(
        res,
        turns_used=1,
        max_turns=10,
        clarification_answer=res.clarification_answer,
        resume_comment=res.resume_comment,
    )
    assert "A novel logistics tracker" in prompt
    assert "10,000 trucks" in prompt
    assert "Focus on EU markets" in prompt
    assert "## Turn 1 of 10" in prompt


def test_intermittent_error_classifier():
    # Retryable errors
    assert _is_intermittent_error_msg("429 Resource Exhausted") is True
    assert _is_intermittent_error_msg("HTTP 503 service unavailable") is True
    assert _is_intermittent_error_msg("Connection reset by peer: econnreset") is True
    assert _is_intermittent_error(Exception("Read timeout on socket")) is True

    # Non-retryable / fatal errors
    assert _is_intermittent_error_msg("API key not valid. Please pass a valid API key.") is False
    assert _is_intermittent_error_msg("401 Unauthorized") is False
    assert _is_intermittent_error_msg("403 Forbidden") is False
    assert _is_intermittent_error_msg("400 Invalid argument") is False
    assert _is_intermittent_error(asyncio.CancelledError()) is False


def test_build_result_dict():
    rec = RunRecord(run_id="run-123", idea="Test Idea", status="needs_approval")
    res = OrchestratorResult(
        idea="Test Idea",
        prd="# PRD",
        verdict={"verdict": "PROCEED"},
        research_brief="Research brief text",
        advocate_argument="Advocate text",
        critic_rebuttal="Critic text",
        creative_angles="Creative text",
        security_audit={"ok": True},
        turns_used=3,
        events=[{"agent": "Researcher", "text": "brief"}],
    )
    d = _build_result_dict(rec, res)
    assert d["run_id"] == "run-123"
    assert d["status"] == "needs_approval"
    assert d["prd"] == "# PRD"
    assert d["verdict"] == {"verdict": "PROCEED"}
    assert d["research_brief"] == "Research brief text"
    assert d["turns_used"] == 3
    assert len(d["transcript"]) == 1


def test_api_clarify_returns_404_when_no_pause_record(monkeypatch):
    client = TestClient(app)
    # Register an active run
    create_resp = client.post(
        "/api/debates",
        json={"idea": "A simple marketplace", "api_key": "sk-or-v1-fakevalidkeywithsixteenchars"},
    )
    assert create_resp.status_code == 201
    run_id = create_resp.json()["run_id"]

    # clarify on a run with NO paused state on disk should 404
    clarify_resp = client.post(
        f"/api/debates/{run_id}/clarify",
        json={"answer": "Target market is B2B", "api_key": "sk-or-v1-fakevalidkeywithsixteenchars"},
    )
    assert clarify_resp.status_code == 404
    assert "No paused debate found" in clarify_resp.json()["detail"]


def test_run_manager_per_run_isolation():
    rm = run_manager._Manager()
    rm.start("run-A", deadline_seconds=60)
    rm.start("run-B", deadline_seconds=60)

    assert rm.should_stop("run-A") is False
    assert rm.should_stop("run-B") is False

    # Stop run-A only
    rm.stop(reason="user stop", run_id="run-A")

    assert rm.should_stop("run-A") is True
    assert rm.should_stop("run-B") is False


def test_ephemeral_store_uses_configured_ttl():
    store = EphemeralStore(ttl_seconds=100.0)
    assert store.ttl == 100.0
    rec = RunRecord(run_id="test-run-ttl", idea="An idea")
    store.register(rec, now=1000.0)
    # At t=1050 (under TTL), not swept
    swept = store.sweep_ttl(now=1050.0)
    assert swept == []
    assert store.get("test-run-ttl") is not None
    # At t=1101 (over TTL), swept
    swept = store.sweep_ttl(now=1101.0)
    assert swept == ["test-run-ttl"]
    assert store.get("test-run-ttl") is None
    assert store.is_gone("test-run-ttl") is True
