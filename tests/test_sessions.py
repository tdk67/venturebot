"""A5 (G5-G7): server-side session store -- hashed tokens, rotation, revocation.

A DB dump must not yield usable session cookies; logout must invalidate the
token server-side; every login must mint a fresh token (fixation defense).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.sessions import SessionStore


def test_create_and_get_roundtrip(tmp_path):
    s = SessionStore(tmp_path / "s.db")
    token = s.create("a@x.com", name="A", picture="")
    user = s.get(token)
    assert user is not None
    assert user["email"] == "a@x.com"
    assert user["name"] == "A"


def test_only_hash_stored(tmp_path):
    s = SessionStore(tmp_path / "s.db")
    token = s.create("a@x.com")
    # WAL mode: data may live in -wal; scan every db-side file.
    raw = b"".join(p.read_bytes() for p in tmp_path.glob("s.db*"))
    assert token.encode() not in raw  # raw token never persisted
    import hashlib
    assert hashlib.sha256(token.encode()).hexdigest().encode() in raw


def test_revoke_on_logout(tmp_path):
    s = SessionStore(tmp_path / "s.db")
    token = s.create("a@x.com")
    assert s.get(token) is not None
    s.revoke(token)
    assert s.get(token) is None


def test_unknown_or_empty_token_rejected(tmp_path):
    s = SessionStore(tmp_path / "s.db")
    assert s.get("garbage") is None
    assert s.get("") is None
    assert s.revoke("") is None  # no crash


def test_expired_session_purged(tmp_path):
    s = SessionStore(tmp_path / "s.db")
    # Forge an already-expired row directly.
    import time as _t
    conn = _sqlite(tmp_path / "s.db")
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
        ("deadbeef", "old@x.com", "", "", _t.time() - 100, _t.time() - 10),
    )
    conn.commit()
    assert s.get("whatever") is None
    purged = s.purge_expired()
    assert purged == 1


def _sqlite(path):
    import sqlite3
    c = sqlite3.connect(path)
    c.execute(
        """CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, email TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '', picture TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL, expires_at REAL NOT NULL)"""
    )
    return c


def test_rotation_each_login_new_token(tmp_path):
    s = SessionStore(tmp_path / "s.db")
    t1 = s.create("a@x.com")
    t2 = s.create("a@x.com")
    assert t1 != t2
    # Both are independently valid until one is revoked.
    assert s.get(t1) and s.get(t2)


def test_auth_module_uses_store(monkeypatch, tmp_path):
    from src import auth, sessions
    monkeypatch.setattr(sessions, "session_store", SessionStore(tmp_path / "s.db"))
    token = auth.create_session_token("b@x.com", "B", "")
    data = auth.verify_session_token(token)
    assert data and data["email"] == "b@x.com"
    auth.revoke_session(token)
    assert auth.verify_session_token(token) is None
