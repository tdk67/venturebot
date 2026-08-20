"""Tests for the self-improvement memory layer (M3)."""
import json
import time

import pytest

from src.memory.idea_tree import decide_idea, prune_ideas
from src.memory.sqlite_store import MemoryStore
from src.memory import _throttle


@pytest.fixture()
def store(tmp_path):
    s = MemoryStore(db_path=tmp_path / "test.db")
    yield s
    s.close()


# ── MemoryStore CRUD ────────────────────────────────────────────────────
def test_store_creates_tables(store):
    store._ensure_conn()
    tables = {r["name"] for r in store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("session_facts", "agent_lessons", "agent_techniques",
              "user_profile", "idea_tree"):
        assert t in tables


def test_facts_roundtrip_and_filter(store):
    store.save_fact("s1", "Researcher", "agent_message", "hello")
    store.save_fact("s1", "Judge", "verdict", "PROCEED")
    store.save_fact("s2", "Critic", "agent_message", "rebuttal")
    assert len(store.get_facts(session_id="s1")) == 2
    assert len(store.get_facts(session_id="s2")) == 1
    assert len(store.get_facts(since=time.time() - 5)) == 3


def test_lessons_retire_by_name(store):
    store.save_lesson("avoid", "don't hallucinate URLs", "turn 1")
    store.save_lesson("avoid", "don't hallucinate URLs", "turn 2")
    assert len(store.get_lessons(active_only=True)) == 2
    n = store.retire_lessons_by_name("avoid")
    assert n == 2
    assert store.get_lessons(active_only=True) == []


def test_technique_upsert_and_outcome(store):
    t1 = store.save_technique("check-source", "verify before citing")
    t2 = store.save_technique("check-source", "verify URLs + dates")
    assert t1 == t2  # upsert, no duplicate
    assert len(store.get_techniques()) == 1
    store.record_technique_outcome("check-source", success=True)
    store.record_technique_outcome("check-source", success=False)
    tech = store.get_techniques()[0]
    assert tech["success_count"] == 1
    assert tech["failure_count"] == 1


def test_profile_upsert(store):
    store.update_profile({"pref_language": "Kotlin", "style": "terse"})
    store.update_profile({"pref_language": "Python"})
    p = store.get_profile()
    assert p["pref_language"] == "Python"
    assert p["style"] == "terse"


def test_idea_tree_crud(store):
    iid = store.create_idea("app idea")
    store.update_idea_scores(iid, {"novelty": 8, "feasibility": 7, "market_fit": 6})
    store.note_human_intervention(iid)
    idea = store.get_idea(iid)
    assert idea["human_intervention_count"] == 1
    assert idea["status"] == "ACTIVE"
    store.update_idea_status(iid, "PRUNED", "user rejected")
    assert store.get_idea(iid)["status"] == "PRUNED"
    with pytest.raises(ValueError):
        store.update_idea_status(iid, "BOGUS")


# ── Idea tree pruning (PRD §5.5) ────────────────────────────────────────
def _idea(**kw):
    base = {"id": "x", "title": "t", "status": "ACTIVE", "scores": None,
            "human_intervention_count": 0, "updated_at": time.time() - 1}
    base.update(kw)
    return base


def test_prune_low_score_no_intervention_after_24h():
    d = decide_idea(_idea(scores='{"novelty": 2, "feasibility": 3, "market_fit": 4}',
                          updated_at=time.time() - 25 * 3600))
    assert d.action == "PRUNE"


def test_keep_low_score_within_grace():
    d = decide_idea(_idea(scores='{"novelty": 2, "feasibility": 3, "market_fit": 4}',
                          updated_at=time.time() - 60))
    assert d.action == "KEEP"


def test_park_low_score_with_human_intervention():
    d = decide_idea(_idea(scores='{"novelty": 2, "feasibility": 3, "market_fit": 4}',
                          human_intervention_count=1,
                          updated_at=time.time() - 25 * 3600))
    assert d.action == "PARK"


def test_park_inactive_7_days_healthy_score():
    d = decide_idea(_idea(scores='{"novelty": 9, "feasibility": 9, "market_fit": 9}',
                          updated_at=time.time() - 8 * 24 * 3600))
    assert d.action == "PARK"


def test_prune_parked_30_days():
    d = decide_idea(_idea(status="PARK", updated_at=time.time() - 31 * 24 * 3600))
    assert d.action == "PRUNE"


def test_keep_healthy_active():
    d = decide_idea(_idea(scores='{"novelty": 8, "feasibility": 8, "market_fit": 8}',
                          updated_at=time.time() - 10))
    assert d.action == "KEEP"


def test_prune_ideas_collects_changes():
    ideas = [
        _idea(id="a", scores='{"novelty": 2, "feasibility": 3, "market_fit": 4}',
              updated_at=time.time() - 25 * 3600),
        _idea(id="b", scores='{"novelty": 9, "feasibility": 9, "market_fit": 9}',
              updated_at=time.time() - 10),
    ]
    result = prune_ideas(ideas)
    changes = result.changes()
    assert {"idea_id": "a", "status": "PRUNE"} in [
        {"idea_id": c["idea_id"], "status": c["status"]} for c in changes]


# ── Throttle ────────────────────────────────────────────────────────────
def test_throttle_cooldown():
    state = {}
    assert _throttle.try_claim(state, "auto_capture") is True
    assert _throttle.try_claim(state, "auto_capture") is False  # within cooldown
    assert _throttle.try_claim(state, "review_fork") is True   # different fork type


def test_throttle_none_state_always_claims():
    assert _throttle.try_claim(None, "auto_capture") is True


def test_throttle_cap(monkeypatch):
    monkeypatch.setattr(_throttle, "_PER_SESSION_CAP", 2)
    monkeypatch.setattr(_throttle, "_cooldown_seconds", lambda: 0.0)  # disable cooldown
    state = {}
    assert _throttle.try_claim(state, "x") is True
    assert _throttle.try_claim(state, "x") is True
    assert _throttle.try_claim(state, "x") is False  # cap reached
