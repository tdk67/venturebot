"""Tests for review_fork analysis + scheduler (M3, non-live)."""
import pytest

from venturebot.memory import review_fork
from venturebot.memory.sqlite_store import MemoryStore


@pytest.fixture()
def store(tmp_path):
    s = MemoryStore(db_path=tmp_path / "test.db")
    yield s
    s.close()


def test_extract_json_plain():
    assert review_fork._extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_in_prose():
    text = 'Here you go: {"reinforce": ["x"]} thanks'
    assert review_fork._extract_json(text) == {"reinforce": ["x"]}


def test_extract_json_garbage_returns_none():
    assert review_fork._extract_json("") is None
    assert review_fork._extract_json("no json here") is None


def test_build_prompt_includes_lessons():
    p = review_fork.build_prompt("transcript", [{"name": "n", "rule": "r"}])
    assert "transcript" in p
    assert "n: r" in p


def test_apply_review_adds_technique_and_lessons(store):
    analysis = {
        "new_technique": {"name": "verify-source", "rule": "check URLs"},
        "retire_technique": None,
        "avoid": ["don't guess"],
        "reinforce": ["be concise"],
    }
    summary = review_fork.apply_review(store, analysis)
    assert "verify-source" in summary["techniques_added"]
    assert len(summary["lessons_added"]) == 2
    assert store.get_techniques()[0]["name"] == "verify-source"
    assert len(store.get_lessons(active_only=True)) == 2


def test_apply_review_retires_technique(store):
    store.save_technique("old-tech", "outdated")
    summary = review_fork.apply_review(store, {"retire_technique": "old-tech"})
    assert "old-tech" in summary["techniques_retired"]
    assert store.get_techniques(active_only=True) == []


def test_analyze_turn_throttled_returns_none(store):
    # First call claims the slot; second (within cooldown) is skipped.
    throttle = {}
    # inject a fake llm_call so no network is touched
    fake_llm = lambda prompt: '{"new_technique": {"name": "t", "rule": "r"}}'
    r1 = review_fork.analyze_turn(store, "hello", throttle, llm_call=fake_llm)
    assert r1 is not None
    r2 = review_fork.analyze_turn(store, "hello", throttle, llm_call=fake_llm)
    assert r2 is None  # throttled


def test_analyze_turn_empty_transcript_returns_none(store):
    assert review_fork.analyze_turn(store, "   ") is None


def test_scheduler_disabled_by_default(monkeypatch):
    import venturebot.scheduler as sched
    monkeypatch.setenv("VENTUREBOT_ENABLE_SCHEDULER", "0")
    assert sched.start_scheduler() is False
