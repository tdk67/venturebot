"""Checkpoint persistence tests (IDEA_HISTORY_ADDENDUM T1). Non-live — no LLM."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.agents.pipeline import (
    DebateResult,
    _archive_path,
    _checkpoint_path,
    _result_from_snapshot,
    _snapshot_phase_rank,
    finalize_checkpoint,
    list_checkpoints,
    load_checkpoint,
    save_checkpoint,
)
from src import config, run_manager


@pytest.fixture(autouse=True)
def tmp_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHECKPOINT_DIR", tmp_path / "checkpoints")
    monkeypatch.setattr(config, "ARCHIVE_DIR", tmp_path / "archives")
    return tmp_path


def _result(**kw):
    r = DebateResult(idea="A CLI tool for git diffs")
    r.research_brief = "## Research Brief\n\nMarket is good."
    r.advocate_argument = "## Argument\n\nThis is a great idea."
    r.events = [{"agent": "Researcher", "text": "brief"}]
    r.status = "running"
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def test_save_and_load_checkpoint_roundtrip(tmp_dirs):
    run_id = "run-123"
    run_manager.manager.start(run_id)
    save_checkpoint(_result(), "critic")
    snap = load_checkpoint(run_id)
    assert snap["run_id"] == run_id
    assert snap["current_phase"] == "critic"
    assert snap["research_brief"] == "## Research Brief\n\nMarket is good."
    assert snap["advocate_argument"] == "## Argument\n\nThis is a great idea."
    assert snap["events"] == [{"agent": "Researcher", "text": "brief"}]


def test_save_checkpoint_skips_empty_events(tmp_dirs):
    run_manager.manager.start("run-empty")
    r = DebateResult(idea="x")
    save_checkpoint(r, "advocate")
    assert load_checkpoint("run-empty") is None  # no events -> no write


def test_checkpoint_is_atomic_and_replaces(tmp_dirs):
    run_id = "run-atomic"
    run_manager.manager.start(run_id)
    save_checkpoint(_result(), "advocate")
    save_checkpoint(_result(critic_rebuttal="rebuttal"), "judge")
    snap = load_checkpoint(run_id)
    assert snap["current_phase"] == "judge"
    assert snap["critic_rebuttal"] == "rebuttal"
    # no stray temp files left behind
    leftovers = [p for p in config.CHECKPOINT_DIR.glob("*") if p != _checkpoint_path(run_id)]
    assert leftovers == []


def test_list_checkpoints_metadata(tmp_dirs):
    run_manager.manager.start("run-list")
    save_checkpoint(_result(), "advocate")
    snaps = list_checkpoints()
    assert len(snaps) == 1
    assert snaps[0]["run_id"] == "run-list"
    assert snaps[0]["phase"] == "advocate"
    assert snaps[0]["status"] == "running"


def test_finalize_moves_to_archive(tmp_dirs):
    run_id = "run-final"
    run_manager.manager.start(run_id)
    save_checkpoint(_result(), "needs_approval")
    finalize_checkpoint(run_id)
    assert not _checkpoint_path(run_id).exists()
    assert _archive_path(run_id).exists()
    archived = json.loads(_archive_path(run_id).read_text())
    assert archived["run_id"] == run_id


def test_load_missing_returns_none(tmp_dirs):
    assert load_checkpoint("nope") is None


def test_result_from_snapshot_reconstructs(tmp_dirs):
    snap = {
        "idea": "idea", "idea_id": "iid", "research_brief": "b",
        "advocate_argument": "a", "critic_rebuttal": "c", "verdict_text": "v",
        "verdict": {"verdict": "PROCEED"}, "prd": "p", "security_audit": {"ok": True},
        "status": "needs_approval", "error": None, "events": [{"agent": "x", "text": "y"}],
    }
    r = _result_from_snapshot(snap)
    assert r.idea == "idea"
    assert r.idea_id == "iid"
    assert r.verdict == {"verdict": "PROCEED"}
    assert r.status == "needs_approval"
    assert r.events == [{"agent": "x", "text": "y"}]


def test_snapshot_phase_rank_ordering():
    order = ["research", "advocate", "critic", "judge", "verdict",
             "prd_writer", "auditor", "needs_approval"]
    ranks = [_snapshot_phase_rank(p) for p in order]
    assert ranks == sorted(ranks)
    assert _snapshot_phase_rank("bogus") == -1
