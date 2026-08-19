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
import os
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import auth, budget, config, run_manager, store
from .agents.pipeline import DebateResult, paused_run_ids, resume_debate, run_debate
from .memory.dream_review import run_dream_review
from .memory.sqlite_store import get_store
from .steering import SteeringInbox

app = FastAPI(title="VentureBot Command Center")

# Start the nightly dream-review scheduler (no-op unless VENTUREBOT_ENABLE_SCHEDULER=1).
@app.on_event("startup")
async def _startup():
    from . import scheduler
    scheduler.start_scheduler()

# In-memory SSE fan-out (per-client queues). Max 50 concurrent clients.
_MAX_SSE_CLIENTS = 50
_SSE_CLIENTS: set[asyncio.Queue] = set()

# Shared steering inbox — drained at checkpoints, never mid-turn
_inbox = SteeringInbox()


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
    # secure=True when behind HTTPS (production), False for local dev
    is_secure = request.url.scheme == "https" or os.environ.get("VENTUREBOT_SECURE_COOKIES", "").lower() in ("1", "true", "yes")
    resp.set_cookie(
        "vb_session", token,
        httponly=True, samesite="lax", secure=is_secure,
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

    # Optional user-provided research URLs
    urls = data.get("urls", [])
    if urls:
        _inbox.add_urls(urls)

    asyncio.create_task(_run_phase1_loop(idea))
    return {"status": "started"}


async def _run_phase1_loop(idea: str):
    await _broadcast("run_started", {"idea": idea})
    result = await run_debate(idea, inbox=_inbox)
    await _broadcast("run_finished", {
        "status": result.status,
        "verdict": result.verdict,
        "has_prd": bool(result.prd),
        "prd": result.prd,
        "error": result.error,
    })


# ── Steering + HITL resume ───────────────────────────────────────────
@app.post("/api/steering")
async def api_steering(request: Request):
    """Queue steering guidance (ingested at the next checkpoint)."""
    auth.get_current_user(request)
    data = await request.json()
    text = data.get("text", "").strip()
    urls = data.get("urls", [])
    if text:
        _inbox.add_steering(text)
    if urls:
        _inbox.add_urls(urls)
    await _broadcast("steering_queued", _inbox.snapshot())
    return {"status": "queued", **_inbox.snapshot()}


@app.get("/api/steering")
async def api_steering_status(request: Request):
    auth.get_current_user(request)
    return _inbox.snapshot()


@app.post("/api/resume")
async def api_resume(request: Request):
    """Resume a paused debate with a human decision + optional steering."""
    auth.get_current_user(request)
    data = await request.json()
    run_id = data.get("run_id", "").strip()
    decision = data.get("decision", "").strip().lower()
    if not run_id or not decision:
        raise HTTPException(400, "run_id and decision are required")
    steering = data.get("steering", "").strip() or None
    urls = data.get("urls", [])

    async def _resume_loop():
        try:
            result = await resume_debate(run_id, decision, steering=steering, urls=urls)
            await _broadcast("run_finished", {
                "status": result.status,
                "verdict": result.verdict,
                "has_prd": bool(result.prd),
                "prd": result.prd,
                "error": result.error,
            })
        except KeyError as e:
            await _broadcast("error", {"message": str(e)})

    asyncio.create_task(_resume_loop())
    return {"status": "resuming", "run_id": run_id}


@app.get("/api/paused")
async def api_paused(request: Request):
    auth.get_current_user(request)
    return {"paused_runs": paused_run_ids()}


# ── Self-improvement (M3) ────────────────────────────────────────────
@app.get("/api/memories")
async def api_memories(request: Request):
    """Snapshot of the self-improvement state (PRD §6.1 right panel)."""
    auth.get_current_user(request)
    s = get_store()
    return {
        "lessons": s.get_lessons(active_only=True, limit=50),
        "techniques": s.get_techniques(active_only=True),
        "profile": s.get_profile(),
        "idea_tree": s.get_idea_tree(),
    }


@app.post("/scheduler/dream-review")
async def api_dream_review(request: Request):
    """Manually trigger the nightly consolidation (PRD §5.4)."""
    auth.get_current_user(request)
    summary = await asyncio.to_thread(run_dream_review)
    await _broadcast("dream_review", summary)
    return summary


# ── SSE stream ─────────────────────────────────────────────────────────
@app.get("/api/events")
async def api_events(request: Request):
    auth.get_current_user(request)
    if len(_SSE_CLIENTS) >= _MAX_SSE_CLIENTS:
        raise HTTPException(503, "Too many SSE clients — try again later")
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
