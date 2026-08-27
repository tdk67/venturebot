"""T1 — API contract for the rewritten near-stateless backend.

Verification points (REWRITE_PLAN.md Part C, T1):
  * every new route exercised (happy path + unknown-ID 404)
  * legacy admin/ideas/auth routes → 404 (S5: no identity to gate them with)
  * create-run with a blank/absent api_key → 400 "api_key required" (S1/D1)
  * GET status of a random UUID-shaped run id → 404 (S3: no enumeration)
  * no list/enumeration endpoint exists
  * OpenAPI snapshot is reproducible and has no legacy routes

These tests MUST fail before the API is implemented, and pass after.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from src.dashboard import app

client = TestClient(app)


# -- Legacy routes that must NOT exist in the rewrite (S5 + old app) -------
_LEGACY_PATHS = [
    # S5 admin endpoints (CRITICAL)
    "/api/budget/raise",
    "/api/reset",
    "/api/stop",
    "/scheduler/dream-review",
    # extra admin surface (budget status, usage)
    "/api/usage",
    # auth (NO_AUTH is permanent)
    "/api/auth/login",
    "/api/auth/callback",
    "/api/auth/client-id",
    "/api/auth/logout",
    "/api/auth/me",
    # legacy server-side idea store (D2: no idea table)
    "/api/ideas",
    "/api/ideas/facets",
    "/api/ideas/csv",
    "/api/ideas/export",
    "/api/ideas/import",
    "/api/ideas/duplicate-check",
    # legacy run/control/steering surface
    "/api/run-phase1",
    "/api/state",
    "/api/steering",
    "/api/resume",
    "/api/paused",
    "/api/checkpoints",
    "/api/clarify/answer",
    "/api/feedback",
    "/api/memories",
]


@pytest.mark.parametrize("path", _LEGACY_PATHS)
def test_legacy_routes_are_gone(path: str):
    """Every legacy admin/auth/ideas route returns 404 (route removed)."""
    r = client.get(path)
    assert r.status_code == 404, f"legacy GET {path} should be 404, got {r.status_code}"
    r = client.post(path)
    assert r.status_code == 404, f"legacy POST {path} should be 404, got {r.status_code}"


# -- Health ----------------------------------------------------------------
def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ok"


# -- create-run happy path ------------------------------------------------
def test_create_debate_requires_api_key():
    # blank api_key → 400, always (D1: BYOK, no fallback)
    r = client.post("/api/debates", json={"idea": "a git diff summarizer"})
    assert r.status_code == 400
    assert "api_key" in r.json()["detail"].lower()


def test_create_debate_requires_idea():
    r = client.post("/api/debates", json={"api_key": "sk-or-v1-test"})
    assert r.status_code == 400
    assert "idea" in r.json()["detail"].lower()


def test_create_debate_happy_path():
    r = client.post(
        "/api/debates",
        json={"idea": "a CLI that summarizes git diffs", "api_key": "sk-or-v1-test"},
    )
    assert r.status_code == 201
    body = r.json()
    run_id = body.get("run_id")
    assert isinstance(run_id, str) and len(run_id) > 0
    # run_id must be a real UUIDv4 (S3: 122-bit unguessable)
    parsed = uuid.UUID(run_id)
    assert parsed.version == 4
    assert body.get("status") == "queued"


# -- status endpoint -------------------------------------------------------
def test_status_unknown_id_404():
    r = client.get(f"/api/debates/{uuid.uuid4()}")
    assert r.status_code == 404


def test_status_known_id_200():
    created = client.post(
        "/api/debates",
        json={"idea": "status probe", "api_key": "sk-or-v1-test"},
    ).json()
    run_id = created["run_id"]
    r = client.get(f"/api/debates/{run_id}")
    assert r.status_code == 200
    body = r.json()
    assert body.get("run_id") == run_id
    assert body.get("status") in {"queued", "running", "failed", "done"}


# -- SSE events endpoint ---------------------------------------------------
def test_events_endpoint_serves_sse():
    created = client.post(
        "/api/debates",
        json={"idea": "events probe", "api_key": "sk-or-v1-test"},
    ).json()
    run_id = created["run_id"]

    # TestClient.stream() deadlocks on an infinite SSE generator (the portal
    # never lets request.is_disconnected() resolve). Drive the endpoint's
    # generator directly with a fake request that reports disconnected after
    # the first frame — this asserts the stream is live and terminates cleanly.
    from src.dashboard import api_debate_events
    import asyncio

    class _FakeReq:
        async def is_disconnected(self):
            return True

    async def _drive():
        resp = await api_debate_events(run_id, _FakeReq())
        body = resp.body_iterator
        first = await body.__anext__()
        assert first.startswith("event: hello\n"), first
        assert "text/event-stream" in (resp.media_type or "")
        # after the client disconnects, the loop must exit (no infinite hang)
        try:
            await body.__anext__()
            raise AssertionError("SSE generator should end after disconnect")
        except StopAsyncIteration:
            pass

    asyncio.run(_drive())


# -- result endpoint -------------------------------------------------------
def test_result_unknown_id_404():
    r = client.get(f"/api/debates/{uuid.uuid4()}/result")
    assert r.status_code == 404


def test_result_queued_is_not_ready():
    created = client.post(
        "/api/debates",
        json={"idea": "result probe", "api_key": "sk-or-v1-test"},
    ).json()
    r = client.get(f"/api/debates/{created['run_id']}/result")
    assert r.status_code == 202  # not ready yet


# -- result ACK ------------------------------------------------------------
def test_result_ack_unknown_id_404():
    r = client.post(f"/api/debates/{uuid.uuid4()}/result/ack")
    assert r.status_code == 404


# -- clarify ---------------------------------------------------------------
def test_clarify_unknown_id_404():
    r = client.post(
        f"/api/debates/{uuid.uuid4()}/clarify",
        json={"answer": "focus on solo consultants"},
    )
    assert r.status_code == 404


# -- byok verify -----------------------------------------------------------
def test_byok_verify_missing_key():
    r = client.post("/api/byok/verify", json={})
    assert r.status_code == 400


def test_byok_verify_unrecognized_format():
    r = client.post("/api/byok/verify", json={"api_key": "not-a-real-key"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("valid") is False


# -- no list/enumeration endpoint (S3) ------------------------------------
def test_no_list_endpoint():
    """The app must expose no route that lists or enumerates runs/ideas."""
    routes = {
        getattr(route, "path", "")
        for route in app.routes
        if getattr(route, "path", "").startswith("/api")
    }
    # Only the fixed new surface may exist.
    allowed = {
        "/api/health",
        "/api/debates",
        "/api/debates/{run_id}",
        "/api/debates/{run_id}/events",
        "/api/debates/{run_id}/result",
        "/api/debates/{run_id}/result/ack",
        "/api/debates/{run_id}/clarify",
        "/api/byok/verify",
    }
    unexpected = routes - allowed
    assert not unexpected, f"unexpected API routes: {sorted(unexpected)}"


# -- OpenAPI snapshot (reviewed on change) --------------------------------
_SNAPSHOT = Path(__file__).resolve().parent / "openapi_snapshot.json"


def test_openapi_snapshot_reproducible():
    """OpenAPI schema is deterministic, has no legacy paths, and matches the
    committed snapshot (regenerating with `pytest --regenerate-openapi`)."""
    snap1 = json.dumps(app.openapi(), sort_keys=True)
    snap2 = json.dumps(app.openapi(), sort_keys=True)
    assert snap1 == snap2
    schema = json.loads(snap1)
    api_paths = [p for p in schema.get("paths", {}) if p.startswith("/api")]
    for legacy in ("/api/reset", "/api/stop", "/api/budget/raise",
                   "/scheduler/dream-review", "/api/ideas", "/api/auth/login",
                   "/api/run-phase1", "/api/events"):
        assert legacy not in schema.get("paths", {}), f"legacy path leaked into OpenAPI: {legacy}"
    for required in ("/api/health", "/api/debates", "/api/byok/verify"):
        assert required in schema.get("paths", {}), f"missing required path in OpenAPI: {required}"
    # committed snapshot: diffs must be reviewed on change
    assert _SNAPSHOT.exists(), "tests/openapi_snapshot.json missing — regenerate with `pytest --regenerate-openapi`"
    committed = json.dumps(json.loads(_SNAPSHOT.read_text()), sort_keys=True)
    assert committed == snap1, (
        "OpenAPI changed vs tests/openapi_snapshot.json. "
        "Review the diff, then regenerate with `pytest --regenerate-openapi`."
    )
