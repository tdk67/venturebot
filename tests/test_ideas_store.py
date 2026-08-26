"""Idea store + tagging tests (IDEA_HISTORY_ADDENDUM T3). Non-live."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.memory.sqlite_store import MemoryStore
from src.memory.tagging import extract_tags


@pytest.fixture()
def store(tmp_path):
    s = MemoryStore(db_path=tmp_path / "test.db")
    yield s
    s.close()


def test_update_idea_content_partial(store):
    iid = store.create_idea("an ai cli tool for git")
    store.update_idea_content(iid, research_brief="brief")
    idea = store.get_idea(iid)
    assert idea["research_brief"] == "brief"
    assert idea["prd_text"] is None
    assert idea["debate_transcript"] is None
    assert idea["verdict"] is None
    assert idea["workspace_path"] is None


def test_update_idea_content_idempotent(store):
    iid = store.create_idea("idea")
    store.update_idea_content(iid, research_brief="v1", prd_text="p1")
    store.update_idea_content(iid, research_brief="v2")
    idea = store.get_idea(iid)
    # research_brief overwritten, prd_text untouched
    assert idea["research_brief"] == "v2"
    assert idea["prd_text"] == "p1"


def test_update_idea_content_noop_when_empty(store):
    iid = store.create_idea("idea")
    store.update_idea_content(iid)
    idea = store.get_idea(iid)
    assert idea["updated_at"] == idea["created_at"]  # untouched


def test_update_idea_content_all_fields(store):
    iid = store.create_idea("idea")
    store.update_idea_content(
        iid,
        research_brief="b",
        debate_transcript=json.dumps([{"agent": "Judge", "text": "PROCEED"}]),
        prd_text="p",
        verdict="PROCEED",
        workspace_path="runs/x/",
    )
    idea = store.get_idea(iid)
    assert idea["research_brief"] == "b"
    assert idea["prd_text"] == "p"
    assert idea["verdict"] == "PROCEED"
    assert idea["workspace_path"] == "runs/x/"
    assert json.loads(idea["debate_transcript"])[0]["agent"] == "Judge"


def test_verdict_column_migration(store):
    # The verdict column is added via ALTER TABLE migration; verify it exists
    # and is usable even on an already-created schema.
    store._ensure_conn()
    cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(idea_tree)")}
    assert "verdict" in cols


# -- tag extraction ------------------------------------------------------
def test_extract_tags_empty():
    assert extract_tags() == []
    assert extract_tags(None, "") == []


def test_extract_tags_from_title_and_brief():
    tags = extract_tags("A CLI tool for git diffs", "react dashboard frontend")
    assert "cli" in tags
    assert "devtools" in tags
    assert "frontend" in tags


def test_extract_tags_case_insensitive():
    assert "ai" in extract_tags("Build an AI assistant with GEMINI")


def test_extract_tags_no_false_positives():
    tags = extract_tags("a recipe manager")
    assert tags == []
