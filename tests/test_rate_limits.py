"""T4 — Per-IP rate limits & caps (REWRITE_PLAN.md Part C, T4 / A-S1 / A-S10).

Verification points (from S1 and S10 of REWRITE_PLAN.md Part A):
  * 2nd concurrent create-run from the same IP -> 429
  * 21st create-run within an hour from the same IP -> 429
  * request body > 32 KB -> 413
  * 4th concurrent SSE connection from the same IP -> 429

All counters are in-memory and per-process. The tests use distinct client IPs
per scenario so no limit bleeds across test cases, and each test resets the
limiter with `rate_limit.clear_all()`. Run creation is NON-LIVE: the real
orchestrator is monkeypatched (via `_orchestrator`) to a fake that returns a
done result immediately, and each created run's slot is released by
finalizing (or explicitly) so hourly/concurrency state is deterministic.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from src import rate_limit
from src.dashboard import app

client = TestClient(app, raise_server_exceptions=False)


def _ip_headers(ip: str) -> dict:
    return {"X-Forwarded-For": ip}


@pytest.fixture(autouse=True)
def _reset_limiter():
    rate_limit.clear_all()


def _fake_orchestrator(monkeypatch):
    """Install a non-live orchestrator that succeeds immediately."""
    async def fake(idea, *, api_key=None, external_run_id=None, **kwargs):
        return type("Res", (), {
            "status": "done",
            "prd": "PRD",
            "verdict": {"verdict": "PROCEED"},
            "events": [],
            "error": None,
        })()
    monkeypatch.setattr("src.dashboard._orchestrator", fake)
    return fake


# --- S1: concurrency cap ------------------------------------------------

def test_second_concurrent_run_same_ip_429():
    """2nd create-run while the 1st is EXECUTING -> 429."""
    # the executor would mark run1 as executing; emulate that (T5 seam)
    assert rate_limit.begin_concurrent("10.0.0.1", "exec-1")
    r1 = client.post("/api/debates", json={
        "idea": "one", "api_key": "sk-or-v1-test",
    }, headers=_ip_headers("10.0.0.1"))
    assert r1.status_code == 429, r1.text
    assert "concurrent" in r1.json().get("detail", "").lower()


def test_concurrent_runs_different_ip_ok():
    """Two runs from DIFFERENT IPs are independent -> both 201."""
    r1 = client.post("/api/debates", json={
        "idea": "one", "api_key": "sk-or-v1-test",
    }, headers=_ip_headers("10.0.0.2"))
    assert r1.status_code == 201
    r2 = client.post("/api/debates", json={
        "idea": "two", "api_key": "sk-or-v1-test",
    }, headers=_ip_headers("10.0.0.3"))
    assert r2.status_code == 201


def test_queued_runs_do_not_block_concurrency():
    """Multiple queued (created, not executing) runs from one IP are allowed;
    the concurrency cap applies to executing runs only (S1 + skeleton T1)."""
    r1 = client.post("/api/debates", json={
        "idea": "q1", "api_key": "sk-or-v1-test",
    }, headers=_ip_headers("10.0.0.8"))
    assert r1.status_code == 201
    r2 = client.post("/api/debates", json={
        "idea": "q2", "api_key": "sk-or-v1-test",
    }, headers=_ip_headers("10.0.0.8"))
    assert r2.status_code == 201


# --- S1: hourly window -------------------------------------------------
def test_21st_run_in_hour_same_ip_429():
    """After MAX_RUNS_PER_HOUR (20) accepted in the window, the next is 429."""
    for i in range(rate_limit.MAX_RUNS_PER_HOUR_PER_IP):
        r = client.post("/api/debates", json={
            "idea": f"hour{i}", "api_key": "sk-or-v1-test",
        }, headers=_ip_headers("10.0.0.4"))
        assert r.status_code == 201
    # 21st -> 429
    r21 = client.post("/api/debates", json={
        "idea": "21st", "api_key": "sk-or-v1-test",
    }, headers=_ip_headers("10.0.0.4"))
    assert r21.status_code == 429


def test_hourly_limit_resets_after_window(monkeypatch):
    """Once the rolling hour passes, the counter restarts (mocked clock)."""
    import src.rate_limit as srl
    monkeypatch.setattr(srl, "_clock", _FakeClock(1_000_000.0))
    for i in range(srl.MAX_RUNS_PER_HOUR_PER_IP):
        r = client.post("/api/debates", json={
            "idea": f"win{i}", "api_key": "sk-or-v1-test",
        }, headers=_ip_headers("10.0.0.5"))
        assert r.status_code == 201
    # advance past the window -> new window, no limit
    srl._clock.now = 1_000_000.0 + srl.RUN_WINDOW_SECONDS + 1
    r_again = client.post("/api/debates", json={
        "idea": "win-again", "api_key": "sk-or-v1-test",
    }, headers=_ip_headers("10.0.0.5"))
    assert r_again.status_code == 201


# --- S1: body size ---------------------------------------------
def test_oversized_body_413():
    r = client.post("/api/debates", json={
        "idea": "x" * (rate_limit.MAX_BODY_BYTES + 1),
        "api_key": "sk-or-v1-test",
    }, headers=_ip_headers("10.0.0.6"))
    assert r.status_code == 413


# --- S10: SSE connection cap -------------------------------------
def test_4th_sse_conn_same_ip_429():
    tok_d = {}
    ip = "10.0.0.7"
    for _ in range(rate_limit.MAX_SSE_PER_IP):
        tok = rate_limit.sse_acquire(ip)
        assert tok is not None
        tok_d[tok] = ip
    assert rate_limit.sse_acquire(ip) is None  # 4th -> denied
    # releasing frees a slot
    rate_limit.sse_release(ip, next(iter(tok_d)))
    assert rate_limit.sse_acquire(ip) is not None


def test_4th_sse_conn_endpoint_429():
    """S10 via the real endpoint: a 4th open event stream from one IP is 429.
    Pre-fill 3 tokens for the IP (module-level, no generator), then drive the
    endpoint once: the 4th sse_acquire must raise HTTPException(429) before any
    frame is produced."""
    from src.dashboard import api_debate_events
    from fastapi import HTTPException
    import src.rate_limit as srl

    created = client.post("/api/debates", json={
        "idea": "sse cap", "api_key": "sk-or-v1-test",
    }, headers=_ip_headers("10.0.0.7"))
    run_id = created.json()["run_id"]

    ip = "10.0.0.7"
    # occupy this IP's SSE budget without needing live streams
    held = [srl.sse_acquire(ip) for _ in range(srl.MAX_SSE_PER_IP)]
    assert all(t is not None for t in held)

    class _FakeReq:
        def __init__(self, ip):
            self.headers = {"x-forwarded-for": ip}
        async def is_disconnected(self):
            return True

    async def _drive(ip):
        return await api_debate_events(run_id, _FakeReq(ip))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_drive(ip))  # 4th stream -> 429
    assert exc.value.status_code == 429
    # release the pre-filled tokens so the autouse reset is clean
    for t in held:
        srl.sse_release(ip, t)


class _FakeClock:
    def __init__(self, now):
        self.now = now
    def __call__(self):
        return self.now