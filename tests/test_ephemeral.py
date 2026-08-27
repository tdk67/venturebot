"""T5 — Ephemeral store + workspace TTL sweep (REWRITE_PLAN.md Part C, T5 / A-S6 / D2).

Verification points (from S6 of REWRITE_PLAN.md Part A + D2):
  * after a run ends AND the sweeper runs, `grep -r` of the idea text over the
    workspace/state dirs -> ZERO matches (idea text never persists on disk)
  * server persists ONLY in-flight run records (with TTL); no idea table
  * per-run workspaces are wiped on TTL sweep / at the end of the run
  * a run still in flight (before TTL) retains its workspace -- only expired
    or acknowledged runs are swept

These tests are NON-LIVE. The filesystem workspace test uses a real temp dir;
the store TTL tests drive a fake clock.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src import dashboard
from src.ephemeral_store import EphemeralStore
from src.inflight_sweeper import sweep_workspaces


class _FakeTick:
    t = 1_000_000.0

    @classmethod
    def now(cls):
        return cls.t

    @classmethod
    def advance(cls, dt):
        cls.t += dt


@pytest.fixture(autouse=True)
def _fresh_store():
    dashboard.STORE = EphemeralStore(watch_plugin=dashboard._emit_plugin)
    dashboard.STORE.tick = _FakeTick.now
    yield dashboard.STORE
    dashboard.STORE.clear()


def _register_done(store, run_id, idea="unique secret idea file"):
    rec = dashboard.RunRecord(run_id=run_id, idea=idea, api_key="")
    rec.status = "done"
    rec.result = {"run_id": run_id, "prd": "# PRD", "verdict": {}}
    store.register(rec, now=_FakeTick.now())
    return store.get(run_id)


# --- D2: no idea table, no listing ----------------------------------------

def test_store_has_no_idea_table():
    store = EphemeralStore(watch_plugin=dashboard._emit_plugin)
    # server-side idea table is forbidden (D2): only per-run boxes exist
    assert not hasattr(store, "ideas")
    assert not callable(getattr(store, "list_runs", None))


# --- S6: idea text must not persist on disk after sweep ---------------------

def test_workspace_wiped_after_sweep(monkeypatch, tmp_path):
    """grep -r of the idea text over workspace/state dirs -> ZERO matches."""
    ws = tmp_path / "workspace"
    monkeypatch.setattr("src.agents.orchestrator.config.WORKSPACE_DIR", ws)
    ws.mkdir(parents=True)

    from src.agents import orchestrator as orch
    rec = dashboard.RunRecord(run_id="ws-1", idea="SUPERSECRET-idea-uuid", api_key="")
    orch._write_workspace_file("RESEARCH_BRIEF.md", "SUPERSECRET-idea-uuid content", run_id="ws-1")

    # sweep the finished run's workspace
    sweep_workspaces({"ws-1"}, root=ws)
    leftover = list((ws / "runs" / "ws-1").rglob("*")) if (ws / "runs" / "ws-1").exists() else []
    assert leftover == [], f"workspace should be wiped: {leftover}"
    # grep over the whole workspace tree for the secret
    hits = []
    for f in ws.rglob("*"):
        if f.is_file():
            try:
                if "SUPERSECRET-idea-uuid" in f.read_text():
                    hits.append(str(f))
            except OSError:
                pass
    assert hits == [], f"idea text leaked into workspace: {hits}"


# --- Store TTL lifecycle ----------------------------------------------------

def test_store_register_get_sweep_ttl():
    _register_done(dashboard.STORE, "r-ttl-a", idea="AA-secret")
    assert dashboard.STORE.get("r-ttl-a") is not None
    _FakeTick.advance(86400)
    dashboard.STORE.sweep_ttl(now=_FakeTick.now())
    assert dashboard.STORE.get("r-ttl-a") is None


def test_store_keeps_fresh_run():
    _register_done(dashboard.STORE, "r-fresh")
    _FakeTick.advance(60)  # within TTL
    dashboard.STORE.sweep_ttl(now=_FakeTick.now())
    assert dashboard.STORE.get("r-fresh") is not None


def test_ack_removes_entry():
    _register_done(dashboard.STORE, "r-ackme")
    assert dashboard.STORE.ack("r-ackme", now=_FakeTick.now()) is True
    assert dashboard.STORE.get("r-ackme") is None


def test_ack_nonexistent_false():
    assert dashboard.STORE.ack("nope", now=_FakeTick.now()) is False


# --- unknown run ids -> 404 (S3) -------------------------------------------

def test_unknown_run_id_not_enumerable():
    # GET any route for a random run id must be 404, never a list
    from fastapi.testclient import TestClient
    from src.dashboard import app
    c = TestClient(app)
    assert c.get("/api/debates/does-not-exist").status_code == 404
    assert c.get("/api/debates/does-not-exist/result").status_code == 404
    assert c.post("/api/debates/does-not-exist/result/ack").status_code == 404


# --- dashboard wired to the store + TTL sweeper -----------------------------

def test_dashboard_sweep_wipes_workspace_and_410s(monkeypatch, tmp_path):
    """End-to-end: a finished run's workspace is wiped by the TTL sweep and the
    result endpoint then answers 410 gone (never 404). This is the actual
    'TTL sweeper' behavior S7/D3/S6 require."""
    ws = tmp_path / "workspace"
    monkeypatch.setattr("src.agents.orchestrator.config.WORKSPACE_DIR", ws)
    ws.mkdir(parents=True)
    from src.agents import orchestrator as orch

    rec = dashboard.RunRecord(run_id="sweep-e2e", idea="TOPSECRET-idea", api_key="")
    rec.status = "done"
    rec.result = {"run_id": "sweep-e2e", "prd": "# PRD TOPSECRET-idea", "verdict": {}}
    orch._write_workspace_file("PRD.md", "TOPSECRET-idea research", run_id="sweep-e2e")
    dashboard.STORE.register(rec, now=_FakeTick.now())
    assert dashboard.STORE.get("sweep-e2e") is not None

    _FakeTick.advance(86400)
    swept = dashboard._sweep_once()  # the periodic sweeper tick
    assert "sweep-e2e" in swept
    assert dashboard.STORE.get("sweep-e2e") is None
    assert "sweep-e2e" not in dashboard._RUNS, "swept run must leave in-memory map too (S6)"
    assert not (ws / "runs" / "sweep-e2e").exists(), "workspace not swept"

    from fastapi.testclient import TestClient
    from src.dashboard import app
    c = TestClient(app)
    assert c.get("/api/debates/sweep-e2e/result").status_code == 410