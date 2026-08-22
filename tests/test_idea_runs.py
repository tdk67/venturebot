"""Tests for per-run idea history (idea_runs) + export/import (P1.1, second brain)."""
import json

import pytest
from fastapi.testclient import TestClient

from src.memory.sqlite_store import MemoryStore


# ── Store: idea_runs CRUD ──────────────────────────────────────────────

def test_idea_run_lifecycle(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    idea_id = s.create_idea("my test idea")

    runs = s.get_idea_runs(idea_id)
    assert runs == []

    run_id = s.start_idea_run(idea_id, comment="competitor X shut down")
    runs = s.get_idea_runs(idea_id)
    assert len(runs) == 1
    assert runs[0]["run_number"] == 1
    assert runs[0]["status"] == "running"
    assert runs[0]["comment"] == "competitor X shut down"

    s.finish_idea_run(
        run_id,
        status="done",
        verdict="PROCEED",
        scores={"novelty": 7, "feasibility": 6, "market_fit": 8},
        research_brief="brief text",
        debate_transcript=json.dumps([{"agent": "Judge", "text": "verdict"}]),
        prd_text="# PRD",
        turns_used=4,
    )
    full = s.get_idea_run(run_id)
    assert full["status"] == "done"
    assert full["verdict"] == "PROCEED"
    assert full["scores"] == {"novelty": 7, "feasibility": 6, "market_fit": 8}
    assert json.loads(full["debate_transcript"])[0]["agent"] == "Judge"
    assert full["prd_text"] == "# PRD"
    assert full["turns_used"] == 4
    assert full["finished_at"] is not None

    # Second run gets run_number 2
    run2 = s.start_idea_run(idea_id)
    runs = s.get_idea_runs(idea_id)
    assert [r["run_number"] for r in runs] == [1, 2]
    assert runs[1]["id"] == run2


def test_idea_run_summary_excludes_blobs(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    idea_id = s.create_idea("blob idea")
    run_id = s.start_idea_run(idea_id)
    s.finish_idea_run(run_id, status="done", prd_text="HUGE PRD",
                      debate_transcript="HUGE TRANSCRIPT",
                      research_brief="BRIEF")
    summary = s.get_idea_runs(idea_id)[0]
    assert "prd_text" not in summary
    assert "debate_transcript" not in summary
    assert "research_brief" not in summary
    full = s.get_idea_runs(idea_id, include_blobs=True)[0]
    assert full["prd_text"] == "HUGE PRD"


def test_delete_idea_cascades_runs(tmp_path):
    s = MemoryStore(tmp_path / "m.db")
    idea_id = s.create_idea("doomed idea")
    s.start_idea_run(idea_id)
    assert s.delete_idea(idea_id) is True
    assert s.get_idea_runs(idea_id) == []


# ── API: run history + replay ──────────────────────────────────────────

@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("VENTUREBOT_NO_AUTH", "1")
    # Isolate the store singleton so API tests never touch the real DB.
    import src.memory.sqlite_store as sqlmod
    test_store = MemoryStore(tmp_path / "api-test.db")
    monkeypatch.setattr(sqlmod, "_singleton", test_store)
    from src import dashboard
    return TestClient(dashboard.app)


def _seed_idea_with_run(client):
    from src.memory.sqlite_store import get_store
    s = get_store()
    idea_id = s.create_idea("seeded idea")
    run_id = s.start_idea_run(idea_id, comment="new market data")
    s.finish_idea_run(
        run_id, status="done", verdict="PROCEED",
        scores={"novelty": 8},
        debate_transcript=json.dumps([
            {"agent": "Human", "text": "new market data"},
            {"agent": "Researcher", "text": "found 3 competitors"},
        ]),
        prd_text="# PRD v1",
    )
    return idea_id, run_id


def test_runs_list_and_detail(client):
    idea_id, run_id = _seed_idea_with_run(client)

    r = client.get(f"/api/ideas/{idea_id}/runs")
    assert r.status_code == 200
    runs = r.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["run_number"] == 1
    assert "debate_transcript" not in runs[0]  # summary excludes blobs

    r = client.get(f"/api/ideas/{idea_id}/runs/{run_id}")
    assert r.status_code == 200
    d = r.json()
    assert d["comment"] == "new market data"
    assert [e["agent"] for e in d["events"]] == ["Human", "Researcher"]
    assert d["prd_text"] == "# PRD v1"

    # detail endpoint also exposes runs now
    r = client.get(f"/api/ideas/{idea_id}")
    assert r.status_code == 200
    assert len(r.json()["runs"]) == 1

    # unknown run → 404
    assert client.get(f"/api/ideas/{idea_id}/runs/nope").status_code == 404
    assert client.get("/api/ideas/nope/runs").status_code == 404


# ── API: export / import ───────────────────────────────────────────────

def test_export_single_idea_json(client):
    idea_id, _ = _seed_idea_with_run(client)
    r = client.get(f"/api/ideas/{idea_id}/export")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["format"] == "venturebot-idea"
    assert data["title"] == "seeded idea"
    assert len(data["runs"]) == 1
    assert data["runs"][0]["debate_transcript"]  # blobs included
    assert json.loads(data["runs"][0]["debate_transcript"])[0]["text"] == "new market data"


def test_export_all_backup(client):
    _seed_idea_with_run(client)
    r = client.get("/api/ideas/export")
    assert r.status_code == 200
    data = r.json()
    assert data["format"] == "venturebot-ideas-backup"
    assert len(data["ideas"]) >= 1


def test_import_roundtrip(client):
    idea_id, _ = _seed_idea_with_run(client)
    exported = client.get(f"/api/ideas/{idea_id}/export").json()

    r = client.post("/api/ideas/import", json=exported)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    new_id = body["ideas"][0]["id"]
    assert new_id != idea_id  # fresh id

    # Full history survived the roundtrip
    runs = client.get(f"/api/ideas/{new_id}/runs").json()["runs"]
    assert len(runs) == 1
    detail = client.get(f"/api/ideas/{new_id}/runs/{runs[0]['id']}").json()
    assert detail["comment"] == "new market data"
    assert [e["agent"] for e in detail["events"]] == ["Human", "Researcher"]
    assert detail["prd_text"] == "# PRD v1"


def test_import_backup_bundle_and_validation(client):
    bundle = {
        "format": "venturebot-ideas-backup",
        "version": 1,
        "ideas": [
            {"format": "venturebot-idea", "title": "idea A", "status": "PARK",
             "runs": [{"run_number": 1, "status": "done", "comment": "c1"}]},
            {"format": "venturebot-idea", "title": "idea B"},
        ],
    }
    r = client.post("/api/ideas/import", json=bundle)
    assert r.status_code == 200
    assert r.json()["count"] == 2

    # bad format rejected
    assert client.post("/api/ideas/import", json={"nope": True}).status_code == 400
    assert client.post("/api/ideas/import", json={"format": "venturebot-idea"}).status_code == 400
