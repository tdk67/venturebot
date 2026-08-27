"""A6 (G6): Google OAuth code flow  -- PKCE + state + nonce + rotation.

The token exchange and id_token verification are mocked; everything else
(state single-use, signup gate, allowlist, session cookie, CSRF header)
runs for real against the FastAPI app.
"""
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from src import config, oauth


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(config, "ALLOWED_EMAILS", [])
    monkeypatch.setattr(config, "SIGNUP_CLOSED", False)
    monkeypatch.setattr(config, "NO_AUTH", False)
    from src.dashboard import app
    return TestClient(app)


# -- begin_login -----------------------------------------------------------

def test_begin_login_builds_pkce_request(client):
    resp = client.get("/api/auth/login", follow_redirects=False)
    assert resp.status_code == 302
    url = urllib.parse.urlparse(resp.headers["location"])
    q = dict(urllib.parse.parse_qsl(url.query))
    assert url.netloc == "accounts.google.com"
    assert q["response_type"] == "code"
    assert q["code_challenge_method"] == "S256"
    assert len(q["code_challenge"]) == 43  # S256 digest, base64url unpadded
    assert q["redirect_uri"].endswith("/api/auth/callback")
    # state registered server-side, single-use
    assert q["state"] in oauth._pending


def test_begin_login_requires_config(monkeypatch, client):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "")
    resp = client.get("/api/auth/login", follow_redirects=False)
    assert resp.status_code == 503


def test_state_is_single_use_and_expiring(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "test-secret")
    oauth._pending.clear()
    url = oauth.begin_login("https://x.example")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    entry = oauth._pending.pop(q["state"])
    # expired states are rejected
    entry["created_at"] -= 3600
    oauth._pending[q["state"]] = entry
    with pytest.raises(Exception, match="[Ii]nvalid or expired"):
        oauth.exchange_code("code", q["state"], "https://x.example")


# -- exchange_code --------------------------------------------------------

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        import json
        return json.dumps(self._payload).encode()


def _fake_google_tokens(monkeypatch, nonce="nonce-ok"):
    """Mock urlopen (token endpoint) + google id_token verification."""
    import json as _json
    import hashlib, base64

    def fake_urlopen(req, timeout=None):
        body = dict(urllib.parse.parse_qsl(req.data.decode()))
        assert body["code_verifier"]  # PKCE verifier sent
        assert body["grant_type"] == "authorization_code"
        # build an unsigned id_token whose `sub` we control via verify mock
        return _FakeResp({"id_token": "fake-id-token-value"})

    def fake_verify(token, request, aud, **kwargs):
        assert aud == "test-client-id"
        return {
            "sub": "sub-123",
            "email": "User@Example.com",
            "email_verified": True,
            "name": "Test User",
            "picture": "",
            "aud": aud,
            "nonce": nonce,
        }

    import google.oauth2.id_token as gidt
    monkeypatch.setattr(gidt, "verify_oauth2_token", fake_verify)
    monkeypatch.setattr(oauth.urllib.request, "urlopen", fake_urlopen)


def test_exchange_code_happy_path(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "test-secret")
    oauth._pending.clear()
    url = oauth.begin_login("https://x.example")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    _fake_google_tokens(monkeypatch, nonce=q["nonce"])
    identity = oauth.exchange_code("code", q["state"], "https://x.example")
    assert identity == {"sub": "sub-123", "email": "user@example.com",
                        "name": "Test User", "picture": ""}


def test_exchange_code_nonce_mismatch(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "test-secret")
    oauth._pending.clear()
    url = oauth.begin_login("https://x.example")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    _fake_google_tokens(monkeypatch, nonce="attacker-nonce")
    with pytest.raises(Exception, match="[Nn]once"):
        oauth.exchange_code("code", q["state"], "https://x.example")


def test_exchange_code_unknown_state_rejected(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "test-secret")
    oauth._pending.clear()
    _fake_google_tokens(monkeypatch)
    with pytest.raises(Exception):
        oauth.exchange_code("code", "never-issued-state", "https://x.example")


# -- callback  -> session ----------------------------------------------------

def _do_callback(client, monkeypatch, email="user@example.com", sub="sub-123"):
    oauth._pending.clear()
    url = oauth.begin_login("http://testserver")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))

    import json as _json

    def fake_urlopen(req, timeout=None):
        return _FakeResp({"id_token": "x"})

    def fake_verify(token, request, aud, **kw):
        return {"sub": sub, "email": email, "email_verified": True,
                "name": "U", "picture": "", "aud": aud, "nonce": q["nonce"]}

    import google.oauth2.id_token as gidt
    monkeypatch.setattr(gidt, "verify_oauth2_token", fake_verify)
    monkeypatch.setattr(oauth.urllib.request, "urlopen", fake_urlopen)

    return client.get(f"/api/auth/callback?code=c&state={q['state']}", follow_redirects=False)


def test_callback_mints_session_cookie(client, monkeypatch):
    resp = _do_callback(client, monkeypatch)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/app"
    set_cookie = resp.headers["set-cookie"]
    assert "vb_session=" in set_cookie
    assert "httponly" in set_cookie.lower()

    # Session actually works: /api/auth/me authenticates.
    cookie = set_cookie.split(";")[0]
    me = client.get("/api/auth/me", cookies={cookie.split("=")[0]: cookie.split("=", 1)[1]})
    assert me.json()["authenticated"] is True


def test_callback_allowlist_blocks(client, monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_EMAILS", ["other@example.com"])
    resp = _do_callback(client, monkeypatch, email="user@example.com")
    assert resp.status_code == 403


def test_signup_closed_blocks_new_user_only(client, monkeypatch):
    monkeypatch.setattr(config, "SIGNUP_CLOSED", True)

    # First login (new user)  -> blocked with 403 (FastAPI converts the raise).
    resp = _do_callback(client, monkeypatch, sub="brand-new-sub", email="fresh@example.com")
    assert resp.status_code == 403

    # Blocked registration must NOT have created a user row.
    from src.sessions import session_store
    assert session_store.get_user("brand-new-sub") is None

    # Pre-existing user  -> allowed despite closed signup.
    from src.sessions import session_store
    session_store.upsert_user("existing-sub", "known@example.com")
    resp = _do_callback(client, monkeypatch, email="known@example.com", sub="existing-sub")
    assert resp.status_code == 302


def test_rotation_two_logins_two_sessions(client, monkeypatch):
    c1 = _do_callback(client, monkeypatch)
    c2 = _do_callback(client, monkeypatch)
    t1 = c1.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    t2 = c2.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    assert t1 != t2  # fresh token per login (fixation defense)


# -- CSRF (G5): cross-site mutating requests blocked ----------------------

def test_cross_site_post_blocked(client):
    resp = client.post("/api/steering", json={"text": "x"},
                       headers={"sec-fetch-site": "cross-site"})
    assert resp.status_code == 403


def test_same_origin_post_allowed(client):
    resp = client.post("/api/steering", json={"text": "x"},
                       headers={"sec-fetch-site": "same-origin"})
    assert resp.status_code != 403


def test_non_browser_post_allowed(client):
    # curl/scripts send no Sec-Fetch-* header at all.
    resp = client.post("/api/steering", json={"text": "x"})
    assert resp.status_code != 403
