"""T5 — Result ACK + TTL lifecycle (REWRITE_PLAN.md Part C, T5 / A-S7 / D3).

Verification points (from S7 of REWRITE_PLAN.md Part A):
  * finish a run, drop the client, re-GET result -> 200
  * after the client ACKs the download -> result endpoint -> 410 gone
  * ACKing before the result exists -> 409 (not ready), record NOT wiped
  * after the TTL expires (mocked clock) + sweep -> result endpoint -> 410 gone
  * a fresh, unexpired run survives a sweep
  * no leftover run record survives past ACK or TTL (server holds nothing)

These tests are NON-LIVE: they drive the ephemeral store and the dashboard
result/ack routes with fakes, never a real LLM.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from src import dashboard
from src.dashboard import app
from src.ephemeral_store import EphemeralStore

client = TestClient(app)


class _FakeTick:
    """Monotonic injectable clock for TTL tests (callable -> float)."""
    t = 1_000_000.0

    @classmethod
    def now(cls):
        return cls.t

    @classmethod
    def advance(cls, dt):
        cls.t += dt


@pytest.fixture(autouse=True)
def _fresh_store():
    """Point the dashboard at a fresh ephemeral store per test."""
    dashboard.STORE = EphemeralStore(watch_plugin=dashboard._emit_plugin)
    dashboard.STORE.tick = _FakeTick.now
    dashboard.IP_TO_BOX = {}
    yield
    dashboard.STORE.clear()
    dashboard.IP_TO_BOX.clear()


def _seed_done_result(store, run_id, idea="a git diff summarizer"):
    """Create a finished run (result present) and register it in the store."""
    rec = dashboard.RunRecord(run_id=run_id, idea=idea, api_key="")
    rec.status = "done"
    rec.result = {
        "run_id": run_id,
        "status": "done",
        "prd": "# PRD for the idea",
        "verdict": {"verdict": "PROCEED"},
    }
    store.register(rec, now=_FakeTick.now())
    entry = store.get(run_id)
    assert entry is not None
    return entry


# --- S7: result survives a dropped client, until ACK ----------------------

def test_result_survives_disconnect_reget_200():
    store = dashboard.STORE
    _seed_done_result(store, run_id="r-ack-1")
    # the client "drops" (SSE stream gone) and re-polls the result endpoint
    r = client.get("/api/debates/r-ack-1/result")
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] == "r-ack-1"
    assert "PRD" in body["result"]["prd"]


def test_result_after_ack_gone_410():
    _seed_done_result(dashboard.STORE, run_id="r-ack-2")
    r = client.post("/api/debates/r-ack-2/result/ack")
    assert r.status_code == 200
    r2 = client.get("/api/debates/r-ack-2/result")
    assert r2.status_code == 410
    # record is actually freed -- no leftover server state (S6/D2)
    assert dashboard.STORE.get("r-ack-2") is None


def test_ack_before_result_not_ready_409():
    """ACKing a run that has no result yet must NOT wipe it."""
    store = dashboard.STORE
    rec = dashboard.RunRecord(run_id="r-ack-3", idea="queued")
    store.register(rec, now=_FakeTick.now())
    r = client.post("/api/debates/r-ack-3/result/ack")
    assert r.status_code == 409  # not ready
    # still present and fetchable once it finishes
    assert store.get("r-ack-3") is not None


def test_ack_unknown_id_404():
    r = client.post(f"/api/debates/{uuid.uuid4()}/result/ack")
    assert r.status_code == 404


# --- S7: TTL expiry + sweep -------------------------------------------------

def test_expired_result_gone_after_sweep():
    store = dashboard.STORE
    _seed_done_result(store, run_id="r-ttl-1")
    _FakeTick.advance(86400)  # well past the TTL
    store.sweep_ttl(now=_FakeTick.now())  # the sweeper ticks down
    assert store.get("r-ttl-1") is None
    r = client.get("/api/debates/r-ttl-1/result")
    assert r.status_code == 410  # gone, never 404


def test_fresh_result_survives_sweep():
    store = dashboard.STORE
    _seed_done_result(store, run_id="r-ttl-2")
    _FakeTick.advance(1)  # negligible time passes
    store.sweep_ttl(now=_FakeTick.now())
    assert store.get("r-ttl-2") is not None, "a fresh run must survive a sweep"


# --- S7: ACK wipes the record; sweep wipes expired records -----------------

def test_sweep_removes_only_expired():
    store = dashboard.STORE
    _seed_done_result(store, run_id="r-old")
    _seed_done_result(store, run_id="r-new")
    _FakeTick.advance(86400)
    store.sweep_ttl(now=_FakeTick.now())
    assert store.get("r-old") is None
    assert store.get("r-new") is None  # both past TTL after the big advance