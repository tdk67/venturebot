"""Dashboard auth + SSE gate tests (S6)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from venturebot import auth  # noqa: E402
from venturebot.dashboard import app  # noqa: E402


client = TestClient(app)


def test_me_unauthenticated():
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["authenticated"] is False


def test_state_requires_auth():
    r = client.get("/api/state")
    assert r.status_code == 401


def test_events_requires_auth():
    r = client.get("/api/events")
    assert r.status_code == 401


def test_stop_requires_auth():
    r = client.post("/api/stop")
    assert r.status_code == 401


def test_authed_state_works():
    token = auth.create_session_token("tdeak67@gmail.com", "T", "")
    r = client.get("/api/state", cookies={"vb_session": token})
    assert r.status_code == 200
    assert "status" in r.json()


def test_google_credential_rejects_garbage():
    r = client.post("/api/auth/google", json={"credential": "not-a-jwt"})
    assert r.status_code in (400, 401, 500)


def test_budget_raise_requires_auth():
    r = client.post("/api/budget/raise", json={"limit": 99})
    assert r.status_code == 401
