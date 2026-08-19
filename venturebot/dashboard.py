"""VentureBot unified dashboard — FastAPI app (M2).

- Google SSO (S6) via /api/auth/* endpoints + signed session cookie
- SSE streaming (Task 9) of the debate events
- HITL gates (Task 7): verdict buttons + PRD approval
- Kill switch + budget status endpoints
- XSS-safe: all model output rendered as text (S9)
"""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import auth, budget, config, run_manager, store
from .agents.pipeline import DebateResult, run_debate

app = FastAPI(title="VentureBot Command Center")

# In-memory SSE fan-out (per-client queues)
_SSE_CLIENTS: set[asyncio.Queue] = set()


def _sse_format(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _broadcast(event: str, data: dict) -> None:
    payload = _sse_format(event, data)
    for q in list(_SSE_CLIENTS):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


# ── Auth routes ────────────────────────────────────────────────────────
@app.get("/api/auth/client-id")
async def auth_client_id():
    return {"client_id": config.GOOGLE_CLIENT_ID}


@app.get("/api/auth/me")
async def auth_me(request: Request):
    try:
        user = auth.get_current_user(request)
        return {"authenticated": True, **user}
    except HTTPException:
        return {"authenticated": False, "email": None}


@app.post("/api/auth/google")
async def auth_google(request: Request):
    data = await request.json()
    credential = data.get("credential", "").strip()
    try:
        user = auth.verify_google_credential(credential)
    except HTTPException as e:
        raise e
    token = auth.create_session_token(user["email"], user["name"], user["picture"])
    resp = JSONResponse({"authenticated": True, **user})
    resp.set_cookie(
        "vb_session", token,
        httponly=True, samesite="lax", secure=False,  # secure=True behind HTTPS
        max_age=30 * 24 * 3600,
    )
    return resp


@app.post("/api/auth/logout")
async def auth_logout():
    resp = JSONResponse({"authenticated": False})
    resp.delete_cookie("vb_session")
    return resp


# ── State + control ────────────────────────────────────────────────────
@app.get("/api/state")
async def api_state(request: Request):
    auth.get_current_user(request)
    state = store.load_state()
    state["budget"] = budget.status()
    return state


@app.post("/api/reset")
async def api_reset(request: Request):
    auth.get_current_user(request)
    store.reset_state()
    return {"status": "reset"}


@app.post("/api/stop")
async def api_stop(request: Request):
    auth.get_current_user(request)
    run_manager.manager.stop("user pressed Stop")
    await _broadcast("stopped", {"message": "Run cancelled by user"})
    return {"status": "stopped"}


@app.post("/api/budget/raise")
async def api_budget_raise(request: Request):
    auth.get_current_user(request)
    data = await request.json()
    new_limit = float(data.get("limit", 0))
    try:
        budget.raise_limit(new_limit)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "ok", **budget.status()}


# ── Run Phase 1 ────────────────────────────────────────────────────────
@app.post("/api/run-phase1")
async def api_run_phase1(request: Request):
    auth.get_current_user(request)
    data = await request.json()
    idea = data.get("idea", "").strip()
    if not idea:
        raise HTTPException(400, "idea is required")

    asyncio.create_task(_run_phase1_loop(idea))
    return {"status": "started"}


async def _run_phase1_loop(idea: str):
    await _broadcast("run_started", {"idea": idea})
    result = await run_debate(idea)
    await _broadcast("run_finished", {
        "status": result.status,
        "verdict": result.verdict,
        "has_prd": bool(result.prd),
        "error": result.error,
    })


# ── SSE stream ─────────────────────────────────────────────────────────
@app.get("/api/events")
async def api_events(request: Request):
    auth.get_current_user(request)
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    _SSE_CLIENTS.add(q)
    async def gen():
        try:
            # initial snapshot
            yield _sse_format("hello", {"state": store.load_state()})
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield msg
                except asyncio.TimeoutError:
                    yield _sse_format("ping", {"t": 0})  # keepalive
        finally:
            _SSE_CLIENTS.discard(q)
    return StreamingResponse(gen(), media_type="text/event-stream")


# ── Dashboard ──────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    # Serve the SPA; auth is enforced client-side via /api/auth/me
    from pathlib import Path
    html = (Path(__file__).parent.parent / "templates" / "index.html").read_text()
    return HTMLResponse(html)
