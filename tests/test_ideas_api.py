"""Ideas + checkpoints API tests (IDEA_HISTORY_ADDENDUM T2). Non-live."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from venturebot import auth
from venturebot.dashboard import app
from venturebot.memory.sqlite_store import MemoryStore


client = TestClient(app)


def _authed():
    token = auth.create_session_token("tdeak67@gmail.com", "T", "")
    return {"vb_session": token}


def _seed(store, n=3):
    ids = []
    for i in range(n):
        iid = store.create_idea(f"idea {i}")
        store.update_idea_scores(iid, {"novelty": 8, "feasibility": 7, "market_fit": 6})
        store.update_idea_content(iid, research_brief=f"brief {i}", verdict="PROCEED")
        ids.append(iid)
    return ids


def test_ideas_requires_auth(monkeypatch):
    r = client.get("/api/ideas")
    assert r.status_code == 401


def test_ideas_list_and_pagination(monkeypatch, tmp_path):
    store = MemoryStore(db_path=tmp_path / "t.db")
    monkeypatch.setattr("venturebot.dashboard.get_store", lambda: store)
    _seed(store, 12)
    r = client.get("/api/ideas", cookies=_authed())
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 12
    assert body["total_pages"] == 2
    assert len(body["items"]) == 10
    r2 = client.get("/api/ideas", params={"page": 2}, cookies=_authed())
    assert len(r2.json()["items"]) == 2
    store.close()


def test_ideas_status_filter(monkeypatch, tmp_path):
    store = MemoryStore(db_path=tmp_path / "t.db")
    monkeypatch.setattr("venturebot.dashboard.get_store", lambda: store)
    ids = _seed(store, 2)
    store.update_idea_status(ids[0], "PARK", "archived")
    r = client.get("/api/ideas", params={"status": "PARK"}, cookies=_authed())
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == ids[0]
    store.close()


def test_ideas_search_filter(monkeypatch, tmp_path):
    store = MemoryStore(db_path=tmp_path / "t.db")
    monkeypatch.setattr("venturebot.dashboard.get_store", lambda: store)
    store.create_idea("a unique widget idea")
    store.create_idea("another thing")
    r = client.get("/api/ideas", params={"search": "widget"}, cookies=_authed())
    body = r.json()
    assert body["total"] == 1
    assert "widget" in body["items"][0]["title"]
    store.close()


def test_idea_detail_includes_prd(monkeypatch, tmp_path):
    store = MemoryStore(db_path=tmp_path / "t.db")
    monkeypatch.setattr("venturebot.dashboard.get_store", lambda: store)
    iid = store.create_idea("detail idea")
    store.update_idea_content(iid, prd_text="# PRD\n\ncontent")
    r = client.get(f"/api/ideas/{iid}", cookies=_authed())
    assert r.status_code == 200
    assert r.json()["prd_text"] == "# PRD\n\ncontent"
    store.close()


def test_idea_detail_404(monkeypatch, tmp_path):
    store = MemoryStore(db_path=tmp_path / "t.db")
    monkeypatch.setattr("venturebot.dashboard.get_store", lambda: store)
    r = client.get("/api/ideas/does-not-exist", cookies=_authed())
    assert r.status_code == 404
    store.close()


def test_idea_archive(monkeypatch, tmp_path):
    store = MemoryStore(db_path=tmp_path / "t.db")
    monkeypatch.setattr("venturebot.dashboard.get_store", lambda: store)
    iid = store.create_idea("park me")
    r = client.post(f"/api/ideas/{iid}/archive", cookies=_authed())
    assert r.status_code == 200
    assert store.get_idea(iid)["status"] == "PARK"
    store.close()


def test_checkpoints_list_requires_auth():
    r = client.get("/api/checkpoints")
    assert r.status_code == 401


def test_checkpoints_list_empty(monkeypatch, tmp_path):
    import venturebot.dashboard as dash
    monkeypatch.setattr(dash, "list_checkpoints", lambda: [])
    r = client.get("/api/checkpoints", cookies=_authed())
    assert r.status_code == 200
    assert r.json() == {"checkpoints": []}
