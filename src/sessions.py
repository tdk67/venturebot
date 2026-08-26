"""Server-side session store (A5 / G5-G7 of the multiuser security review).

Replaces the stateless signed cookie:
  - tokens are random 256-bit values; ONLY sha256 hashes are persisted
    (a DB dump cannot be replayed as a valid cookie);
  - every login creates a FRESH session (rotation  -> session-fixation defense);
  - logout revokes the row; sliding 30-day expiry;
  - expired rows are purged lazily + by the scheduler.

Storage is SQLite under DATA_DIR (same lifecycle as the rest of the app).
Multi-user note: user_id here is the Google sub/email  -- Phase B keys ideas
and debates off the same identity.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time

from . import config

SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 days
_SLIDE_INTERVAL = 24 * 3600  # re-touch at most once a day


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SessionStore:
    def __init__(self, db_path=None):
        self._db_path = str(db_path or (config.DATA_DIR / "sessions.db"))
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                        token_hash TEXT PRIMARY KEY,
                        user_id    TEXT NOT NULL DEFAULT '',
                        email      TEXT NOT NULL,
                        name       TEXT NOT NULL DEFAULT '',
                        picture    TEXT NOT NULL DEFAULT '',
                        created_at REAL NOT NULL,
                        expires_at REAL NOT NULL
                   )"""
            )
            # Migration for pre-A6 rows (user_id added later).
            cols = [r[1] for r in self._conn.execute("PRAGMA table_info(sessions)")]
            if "user_id" not in cols:
                self._conn.execute("ALTER TABLE sessions ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
            # Users (Phase B): user_id is the stable Google `sub`  -- the
            # tenancy primary key every multi-user route will check against.
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS users (
                        user_id     TEXT PRIMARY KEY,  -- google sub
                        email       TEXT NOT NULL UNIQUE,
                        name        TEXT NOT NULL DEFAULT '',
                        picture     TEXT NOT NULL DEFAULT '',
                        created_at  REAL NOT NULL,
                        last_login  REAL NOT NULL
                   )"""
            )
            self._conn.commit()
        return self._conn

    def get_user(self, user_id: str) -> dict | None:
        with self._lock:
            row = self._connect().execute(
                "SELECT user_id, email, name, picture FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        return {"user_id": row[0], "email": row[1], "name": row[2], "picture": row[3]}

    def get_user_by_email(self, email: str) -> dict | None:
        with self._lock:
            row = self._connect().execute(
                "SELECT user_id, email, name, picture FROM users WHERE email = ?",
                (email.lower(),),
            ).fetchone()
        if row is None:
            return None
        return {"user_id": row[0], "email": row[1], "name": row[2], "picture": row[3]}

    def upsert_user(self, user_id: str, email: str, name: str = "", picture: str = "") -> dict:
        """Insert or refresh a user. Returns {'user': ..., 'is_new': bool}."""
        now = time.time()
        existing = self.get_user(user_id)
        with self._lock:
            conn = self._connect()
            if existing:
                conn.execute(
                    "UPDATE users SET email = ?, name = ?, picture = ?, last_login = ? WHERE user_id = ?",
                    (email.lower(), name, picture, now, user_id),
                )
            else:
                conn.execute(
                    "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, email.lower(), name, picture, now, now),
                )
            conn.commit()
        return {
            "user": {"user_id": user_id, "email": email.lower(), "name": name, "picture": picture},
            "is_new": existing is None,
        }

    def create(self, email: str, name: str = "", picture: str = "", user_id: str = "") -> str:
        """Issue a new session; returns the RAW token (cookie value)."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, email, name, picture, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_hash_token(token), user_id, email, name, picture, now, now + SESSION_TTL_SECONDS),
            )
            conn.commit()
        return token

    def get(self, token: str) -> dict | None:
        """Validate a token; slides expiry. Returns user info or None."""
        if not token:
            return None
        th = _hash_token(token)
        now = time.time()
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT user_id, email, name, picture, expires_at FROM sessions WHERE token_hash = ?",
                (th,),
            ).fetchone()
            if row is None:
                return None
            user_id, email, name, picture, expires_at = row
            if expires_at < now:
                self._delete(th)
                return None
            # Sliding window: extend when past the re-touch interval.
            if expires_at < now + SESSION_TTL_SECONDS - _SLIDE_INTERVAL:
                conn.execute(
                    "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                    (now + SESSION_TTL_SECONDS, th),
                )
                conn.commit()
        return {"user_id": user_id, "email": email, "name": name, "picture": picture}

    def revoke(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            conn = self._connect()
            self._delete(_hash_token(token))

    def purge_expired(self) -> int:
        now = time.time()
        with self._lock:
            conn = self._connect()
            cur = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            conn.commit()
            return cur.rowcount

    def _delete(self, token_hash: str) -> None:
        self._connect().execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        self._connect().commit()


# Module-level singleton (per-process; matches store/run_manager patterns).
session_store = SessionStore()
