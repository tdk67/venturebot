"""SQLite memory store — M3 foundation (Task 13).

Single source of truth for the self-improvement layer. Five tables
(PRD §5.5 + §8.1):

  - session_facts     — what happened in each turn
  - agent_lessons     — rules/techniques learned, with evidence + retire flag
  - agent_techniques  — reusable techniques with success/failure counts
  - user_profile      — key/value user preferences + style notes
  - idea_tree         — idea nodes with scores, status, pruning metadata

Connection management: a single sqlite3 connection guarded by a lock
(serialized access). Simple and race-free for a single-user VPS demo;
documented as the swap point for a connection pool / Memory Bank in GCP.

The module exposes a process-wide singleton via `get_store()`, but the
class is fully independent and testable against an in-memory DB.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .. import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_facts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    event_type TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_facts_session ON session_facts(session_id);
CREATE INDEX IF NOT EXISTS idx_session_facts_created ON session_facts(created_at);

CREATE TABLE IF NOT EXISTS agent_lessons (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    rule TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    retired_at REAL
);

CREATE TABLE IF NOT EXISTS agent_techniques (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    when_to_use TEXT NOT NULL DEFAULT '',
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    retired INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user_profile (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS idea_tree (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    scores TEXT,
    research_brief TEXT,
    debate_transcript TEXT,
    prd_text TEXT,
    verdict TEXT,
    workspace_path TEXT,
    human_intervention_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_dream_review REAL,
    pruned_at REAL,
    pruned_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_idea_tree_status ON idea_tree(status);

CREATE TABLE IF NOT EXISTS idea_runs (
    id TEXT PRIMARY KEY,
    idea_id TEXT NOT NULL,
    run_number INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    verdict TEXT,
    scores TEXT,
    research_brief TEXT,
    debate_transcript TEXT,
    prd_text TEXT,
    comment TEXT,
    turns_used INTEGER,
    started_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_idea_runs_idea ON idea_runs(idea_id, run_number);
"""

_VALID_IDEA_STATUSES = {"ACTIVE", "PARK", "PRUNED"}


class MemoryStore:
    """Thread-safe SQLite store for the self-improvement layer."""

    def __init__(self, db_path: Path | str | None = None):
        # ":memory:" (string) → in-memory; None → config.DB_PATH
        self.db_path = str(db_path) if db_path is not None else str(config.DB_PATH)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    # ── connection management ──────────────────────────────────────────
    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA)
            # Lightweight migration for columns added after the initial schema
            # (e.g. idea_tree.verdict). Idempotent: ignore if it already exists.
            try:
                conn.execute("ALTER TABLE idea_tree ADD COLUMN verdict TEXT")
            except sqlite3.OperationalError:
                pass
            conn.commit()
            self._conn = conn
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ── session facts ───────────────────────────────────────────────────
    def save_fact(self, session_id: str, agent: str, event_type: str,
                  content: str, *, now: float | None = None) -> str:
        fact_id = uuid.uuid4().hex
        with self._lock:
            c = self._ensure_conn()
            c.execute(
                "INSERT INTO session_facts (id, session_id, agent, event_type, content, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (fact_id, session_id, agent, event_type, content, now or time.time()),
            )
            c.commit()
        return fact_id

    def get_facts(self, session_id: str | None = None, since: float | None = None,
                  limit: int = 100) -> list[dict]:
        q = "SELECT * FROM session_facts"
        conds, params = [], []
        if session_id is not None:
            conds.append("session_id = ?")
            params.append(session_id)
        if since is not None:
            conds.append("created_at >= ?")
            params.append(since)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._ensure_conn().execute(q, params).fetchall()
        return [dict(r) for r in rows]

    # ── lessons ─────────────────────────────────────────────────────────
    def save_lesson(self, name: str, rule: str, evidence: str = "",
                    *, now: float | None = None) -> str:
        lesson_id = uuid.uuid4().hex
        with self._lock:
            c = self._ensure_conn()
            c.execute(
                "INSERT INTO agent_lessons (id, name, rule, evidence, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (lesson_id, name, rule, evidence, now or time.time()),
            )
            c.commit()
        return lesson_id

    def get_lessons(self, active_only: bool = True, limit: int = 20) -> list[dict]:
        q = "SELECT * FROM agent_lessons"
        if active_only:
            q += " WHERE retired_at IS NULL"
        q += " ORDER BY created_at DESC LIMIT ?"
        with self._lock:
            rows = self._ensure_conn().execute(q, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def retire_lesson(self, lesson_id: str, *, now: float | None = None) -> None:
        with self._lock:
            c = self._ensure_conn()
            c.execute(
                "UPDATE agent_lessons SET retired_at = ? WHERE id = ?",
                (now or time.time(), lesson_id),
            )
            c.commit()

    def retire_lessons_by_name(self, name: str, *, now: float | None = None) -> int:
        """Retire every active lesson whose name matches (case-insensitive)."""
        with self._lock:
            c = self._ensure_conn()
            cur = c.execute(
                "UPDATE agent_lessons SET retired_at = ? "
                "WHERE retired_at IS NULL AND lower(name) = lower(?)",
                (now or time.time(), name),
            )
            c.commit()
            return cur.rowcount

    # ── techniques ──────────────────────────────────────────────────────
    def save_technique(self, name: str, description: str, when_to_use: str = "") -> str:
        with self._lock:
            c = self._ensure_conn()
            existing = c.execute(
                "SELECT id FROM agent_techniques WHERE name = ?", (name,)
            ).fetchone()
            if existing:
                # Upsert: refresh the description rather than duplicate.
                c.execute(
                    "UPDATE agent_techniques SET description = ?, when_to_use = ?, retired = 0 "
                    "WHERE name = ?",
                    (description, when_to_use, name),
                )
                c.commit()
                return existing["id"]
            tech_id = uuid.uuid4().hex
            c.execute(
                "INSERT INTO agent_techniques (id, name, description, when_to_use) "
                "VALUES (?, ?, ?, ?)",
                (tech_id, name, description, when_to_use),
            )
            c.commit()
            return tech_id

    def get_techniques(self, active_only: bool = True) -> list[dict]:
        q = "SELECT * FROM agent_techniques"
        if active_only:
            q += " WHERE retired = 0"
        q += " ORDER BY success_count DESC, name ASC"
        with self._lock:
            rows = self._ensure_conn().execute(q).fetchall()
        return [dict(r) for r in rows]

    def retire_technique(self, name: str) -> int:
        with self._lock:
            c = self._ensure_conn()
            cur = c.execute(
                "UPDATE agent_techniques SET retired = 1 WHERE name = ?", (name,)
            )
            c.commit()
            return cur.rowcount

    def record_technique_outcome(self, name: str, success: bool) -> int:
        col = "success_count" if success else "failure_count"
        with self._lock:
            c = self._ensure_conn()
            cur = c.execute(
                f"UPDATE agent_techniques SET {col} = {col} + 1 WHERE name = ?",
                (name,),
            )
            c.commit()
            return cur.rowcount

    # ── user profile ────────────────────────────────────────────────────
    def update_profile(self, values: dict[str, str]) -> None:
        now = time.time()
        with self._lock:
            c = self._ensure_conn()
            for key, value in values.items():
                c.execute(
                    "INSERT INTO user_profile (key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                    "updated_at = excluded.updated_at",
                    (key, str(value), now),
                )
            c.commit()

    def get_profile(self) -> dict[str, str]:
        with self._lock:
            rows = self._ensure_conn().execute(
                "SELECT key, value FROM user_profile"
            ).fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ── idea tree ───────────────────────────────────────────────────────
    def create_idea(self, title: str, parent_id: str | None = None) -> str:
        idea_id = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            c = self._ensure_conn()
            c.execute(
                "INSERT INTO idea_tree (id, parent_id, title, status, created_at, updated_at) "
                "VALUES (?, ?, ?, 'ACTIVE', ?, ?)",
                (idea_id, parent_id, title, now, now),
            )
            c.commit()
        return idea_id

    def update_idea_scores(self, idea_id: str, scores: dict) -> None:
        with self._lock:
            c = self._ensure_conn()
            c.execute(
                "UPDATE idea_tree SET scores = ?, updated_at = ? WHERE id = ?",
                (json.dumps(scores), time.time(), idea_id),
            )
            c.commit()

    def update_idea_content(
        self,
        idea_id: str,
        *,
        research_brief: str | None = None,
        debate_transcript: str | None = None,
        prd_text: str | None = None,
        verdict: str | None = None,
        workspace_path: str | None = None,
    ) -> None:
        """Idempotent partial update of an idea's content columns (C7).

        Only non-None values are written, so repeated calls are safe and each
        caller updates just the slice it owns. Returns without touching the DB
        if nothing is provided.
        """
        updates: dict[str, object] = {}
        if research_brief is not None:
            updates["research_brief"] = research_brief
        if debate_transcript is not None:
            updates["debate_transcript"] = debate_transcript
        if prd_text is not None:
            updates["prd_text"] = prd_text
        if verdict is not None:
            updates["verdict"] = verdict
        if workspace_path is not None:
            updates["workspace_path"] = workspace_path
        if not updates:
            return
        updates["updated_at"] = time.time()
        assignments = ", ".join(f"{col} = ?" for col in updates)
        with self._lock:
            c = self._ensure_conn()
            c.execute(
                f"UPDATE idea_tree SET {assignments} WHERE id = ?",
                (*updates.values(), idea_id),
            )
            c.commit()

    def update_idea_status(self, idea_id: str, status: str,
                           reason: str | None = None) -> None:
        if status not in _VALID_IDEA_STATUSES:
            raise ValueError(f"Invalid idea status: {status}")
        now = time.time()
        with self._lock:
            c = self._ensure_conn()
            c.execute(
                "UPDATE idea_tree SET status = ?, updated_at = ?, pruned_at = ?, pruned_reason = ? "
                "WHERE id = ?",
                (status, now, now if status == "PRUNED" else None,
                 reason if status == "PRUNED" else None, idea_id),
            )
            c.commit()

    def note_human_intervention(self, idea_id: str) -> None:
        with self._lock:
            c = self._ensure_conn()
            c.execute(
                "UPDATE idea_tree SET human_intervention_count = human_intervention_count + 1, "
                "updated_at = ? WHERE id = ?",
                (time.time(), idea_id),
            )
            c.commit()

    def get_idea_tree(self) -> list[dict]:
        with self._lock:
            rows = self._ensure_conn().execute(
                "SELECT * FROM idea_tree ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_idea(self, idea_id: str) -> dict | None:
        with self._lock:
            row = self._ensure_conn().execute(
                "SELECT * FROM idea_tree WHERE id = ?", (idea_id,)
            ).fetchone()
        return dict(row) if row else None

    def delete_idea(self, idea_id: str) -> bool:
        """Hard-delete an idea from the tree (UI_UX_NOTES #5). Returns True if
        a row was removed. Only safe when the idea is not actively running —
        the caller is responsible for that guard."""
        with self._lock:
            cur = self._ensure_conn().execute(
                "DELETE FROM idea_tree WHERE id = ?", (idea_id,)
            )
            self._ensure_conn().execute(
                "DELETE FROM idea_runs WHERE idea_id = ?", (idea_id,)
            )
            self._ensure_conn().commit()
            return cur.rowcount > 0

    def find_similar_ideas(self, title: str, limit: int = 3) -> list[dict]:
        """Cheap duplicate check (UI_UX_NOTES #4): token-overlap on the title
        against existing ideas, ranked by shared-word count. Deterministic,
        zero LLM cost. Returns the top matches (empty if none overlap)."""
        words = {w for w in title.lower().split() if len(w) > 2}
        if not words:
            return []
        scored = []
        for idea in self.get_idea_tree():
            if (idea.get("status") or "").upper() == "DELETED":
                continue
            other = {w for w in (idea.get("title") or "").lower().split() if len(w) > 2}
            overlap = words & other
            if overlap:
                scored.append((len(overlap), idea))
        scored.sort(key=lambda kv: -kv[0])
        return [idea for _, idea in scored[:limit]]

    # ── idea runs (per-run immutable snapshots — P1.1) ───────────────

    def start_idea_run(self, idea_id: str, comment: str | None = None) -> str:
        """Open a new run row for an idea. Returns the run row id.
        The row stays status='running' until finish_idea_run() completes it,
        so a crashed run is still visible (and distinguishable) in history."""
        import json as _json
        import uuid
        with self._lock:
            conn = self._ensure_conn()
            row = conn.execute(
                "SELECT COALESCE(MAX(run_number), 0) AS n FROM idea_runs WHERE idea_id = ?",
                (idea_id,),
            ).fetchone()
            run_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO idea_runs (id, idea_id, run_number, status, comment, started_at)"
                " VALUES (?, ?, ?, 'running', ?, ?)",
                (run_id, idea_id, (row["n"] or 0) + 1, comment, time.time()),
            )
            conn.commit()
        return run_id

    def finish_idea_run(
        self,
        run_id: str,
        *,
        status: str,
        verdict: str | None = None,
        scores: dict | list | None = None,
        research_brief: str | None = None,
        debate_transcript: str | None = None,
        prd_text: str | None = None,
        turns_used: int | None = None,
    ) -> None:
        """Complete a run row with the final artifacts of that run."""
        import json as _json
        with self._lock:
            conn = self._ensure_conn()
            conn.execute(
                "UPDATE idea_runs SET status = ?, verdict = ?, scores = ?, research_brief = ?,"
                " debate_transcript = ?, prd_text = ?, turns_used = ?, finished_at = ?"
                " WHERE id = ?",
                (
                    status,
                    verdict,
                    _json.dumps(scores) if scores is not None else None,
                    research_brief,
                    debate_transcript,
                    prd_text,
                    turns_used,
                    time.time(),
                    run_id,
                ),
            )
            conn.commit()

    def get_idea_runs(self, idea_id: str, *, include_blobs: bool = False) -> list[dict]:
        """Run history for an idea, oldest first. Heavy blobs (transcript,
        PRD, research brief) are only included when include_blobs=True."""
        cols = (
            "*" if include_blobs else
            "id, idea_id, run_number, status, verdict, scores, comment, turns_used,"
            " started_at, finished_at"
        )
        with self._lock:
            rows = self._ensure_conn().execute(
                f"SELECT {cols} FROM idea_runs WHERE idea_id = ? ORDER BY run_number ASC",
                (idea_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("scores"):
                try:
                    d["scores"] = json.loads(d["scores"])
                except Exception:
                    pass
            out.append(d)
        return out

    def get_idea_run(self, run_id: str) -> dict | None:
        """Full single-run detail including transcript/PRD blobs."""
        with self._lock:
            row = self._ensure_conn().execute(
                "SELECT * FROM idea_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("scores"):
            try:
                d["scores"] = json.loads(d["scores"])
            except Exception:
                pass
        return d

    def delete_idea_runs(self, idea_id: str) -> int:
        """Remove all run rows for an idea (used on hard-delete)."""
        with self._lock:
            cur = self._ensure_conn().execute(
                "DELETE FROM idea_runs WHERE idea_id = ?", (idea_id,)
            )
            self._ensure_conn().commit()
            return cur.rowcount


# ── process-wide singleton ──────────────────────────────────────────────
_singleton: MemoryStore | None = None
_singleton_lock = threading.Lock()


def get_store() -> MemoryStore:
    """Return the process-wide MemoryStore singleton."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = MemoryStore()
        return _singleton
