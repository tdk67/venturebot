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
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from . import auth, budget, config, oauth, run_manager, store
import urllib.parse
from . import gemini_usage
from .agents.orchestrator import (
    OrchestratorResult,
    answer_clarify,
    get_run,
    run_orchestrator,
)
from .memory.dream_review import run_dream_review
from .memory.sqlite_store import get_store
from .memory.tagging import extract_tags
from .steering import SteeringInbox


# Start the nightly dream-review scheduler (no-op unless VENTUREBOT_ENABLE_SCHEDULER=1).
@asynccontextmanager
async def _lifespan(app: FastAPI):
    from . import scheduler
    from .agents.orchestrator import any_pending_pause
    from . import store as _store
    scheduler.start_scheduler()
    # If the server (re)started while a debate was paused on a clarifying
    # question, restore the waiting state so the UI re-offers the answer box.
    if any_pending_pause():
        _store.set_status("waiting_user")
        _store.log("System", "core", "Restored: a debate is paused waiting for your answer.")
    from .sessions import session_store
    session_store.purge_expired()
    yield
    scheduler.stop_scheduler()


app = FastAPI(title="VentureBot Command Center", lifespan=_lifespan)

# ── Security headers (G1–G3, W3/W6 — multiuser security review) ─────────
# Strict CSP: all scripts same-origin (app JS + pinned vendor copies under
# /static/vendor). This kills the CDN-compromise mass-XSS path and shrinks
# the XSS blast radius on IndexedDB data (ideas + BYOK key + tokens).
# style-src keeps 'unsafe-inline': the Tailwind Play runtime injects <style>.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "  # Google profile pictures
    "connect-src 'self'; "
    "font-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    # G5 — CSRF hardening beyond SameSite: browsers always send Sec-Fetch-Site;
    # a cross-site attacker's form/fetch arrives as "cross-site" and is blocked
    # on mutating routes. Non-browser clients (curl/scripts) omit the header
    # and are unaffected.
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
        site = request.headers.get("sec-fetch-site", "").lower()
        if site in ("cross-site",):
            return JSONResponse({"detail": "CSRF check failed"}, status_code=403)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = _CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if config.COOKIE_SECURE:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


from fastapi.staticfiles import StaticFiles  # noqa: E402

app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent.parent / "static")),
    name="static",
)

# In-memory SSE fan-out (per-client queues). Max 50 concurrent clients.
_MAX_SSE_CLIENTS = 50
_SSE_CLIENTS: set[asyncio.Queue] = set()

# Shared steering inbox — drained at checkpoints, never mid-turn
_inbox = SteeringInbox()


# Bridge the in-process event bus → SSE. Subscribed once at import; the
# pipeline never imports the web layer, so this is the only coupling point.
def _sse_event_sink():
    from . import events

    async def _on_event(event: str, payload: dict) -> None:
        await _broadcast(event, payload)

    events.subscribe(_on_event)


_sse_event_sink()


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
def _base_url(request: Request) -> str:
    """Public base URL, trusting nginx's X-Forwarded-* headers."""
    if config.PUBLIC_BASE_URL:
        return config.PUBLIC_BASE_URL
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{proto}://{host}"


def _set_session_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(
        "vb_session", token,
        httponly=True, samesite="lax",
        secure=config.COOKIE_SECURE,
        max_age=30 * 24 * 3600,
    )


@app.get("/api/auth/login")
async def auth_login(request: Request):
    """Start Google OAuth (code flow + PKCE). 302 to accounts.google.com."""
    if config.NO_AUTH:
        return RedirectResponse("/", status_code=302)
    url = oauth.begin_login(_base_url(request))
    return RedirectResponse(url, status_code=302)


@app.get("/api/auth/callback")
async def auth_callback(request: Request):
    """OAuth callback: validate state/nonce/PKCE → mint server-side session.

    Session rotation is inherent: every login creates a FRESH session token.
    """
    from .sessions import session_store

    if config.NO_AUTH:
        return RedirectResponse("/", status_code=302)
    base = _base_url(request)
    params = dict(request.query_params)
    if params.get("error"):
        return RedirectResponse(f"/?login_error={urllib.parse.quote(params['error'])}", status_code=302)
    identity = oauth.exchange_code(params.get("code", ""), params.get("state", ""), base)

    email = identity["email"]
    # Optional operator allowlist still applies when configured.
    if config.ALLOWED_EMAILS and email not in config.ALLOWED_EMAILS:
        raise HTTPException(403, f"Access denied: {email} is not authorized.")

    # Signup gate BEFORE any write: a blocked registration must not leave
    # a user row behind.
    if session_store.get_user(identity["sub"]) is None and config.SIGNUP_CLOSED:
        raise HTTPException(403, "Registrations are currently closed.")

    stored = session_store.upsert_user(identity["sub"], email, identity["name"], identity["picture"])

    user = stored["user"]
    token = auth.create_session_token(user["email"], user["name"], user["picture"])
    resp = RedirectResponse("/", status_code=302)
    _set_session_cookie(resp, token)
    return resp
@app.get("/api/auth/client-id")
async def auth_client_id():
    return {"client_id": config.GOOGLE_CLIENT_ID}


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    # A5/W8: revoke the server-side session row — a stolen cookie value becomes
    # worthless after logout (stateless cookies could not do this).
    token = request.cookies.get("vb_session")
    if token:
        auth.revoke_session(token)
    resp = JSONResponse({"authenticated": False})
    resp.delete_cookie("vb_session")
    return resp


@app.get("/api/auth/me")
async def auth_me(request: Request):
    try:
        user = auth.get_current_user(request)
        return {"authenticated": True, **user}
    except HTTPException:
        return {"authenticated": False, "email": None}


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    # A5/W8: revoke the server-side session row — a stolen cookie value becomes
    # worthless after logout (stateless cookies could not do this).
    token = request.cookies.get("vb_session")
    if token:
        auth.revoke_session(token)
    resp = JSONResponse({"authenticated": False})
    resp.delete_cookie("vb_session")
    return resp


# ── State + control ────────────────────────────────────────────────────
@app.get("/api/state")
async def api_state(request: Request):
    auth.get_current_user(request)
    state = store.load_state()
    state["budget"] = budget.status()
    state["usage"] = gemini_usage.summary()
    # Surface any debate paused on a clarifying question (survives restarts).
    from .agents.orchestrator import any_pending_pause
    pending = any_pending_pause()
    if pending:
        state["pending_clarification"] = {
            "run_id": pending.get("run_id"),
            "question": pending.get("question"),
            "asked_at": pending.get("asked_at"),
            "idea": (pending.get("idea") or "")[:120],
        }
    return state


@app.get("/api/usage")
async def api_usage(request: Request, period: str = "today"):
    """Bucketed LLM call + cost aggregation (UI_UX_NOTES #6).

    period: today (by hour) | week (by day, last 7) | month (by day, last 30).
    Reuses the local spend ledger in gemini_usage.json — no extra LLM cost.
    """
    auth.get_current_user(request)
    import time as _time

    data = gemini_usage._load()
    calls = data.get("calls", [])
    now = _time.time()

    if period == "week":
        window = 7 * 86400
        bucket_s = 86400
        label = lambda ts: _time.strftime("%a", _time.localtime(ts))
    elif period == "month":
        window = 30 * 86400
        bucket_s = 86400
        label = lambda ts: _time.strftime("%d", _time.localtime(ts))
    else:  # today
        window = 86400
        bucket_s = 3600
        label = lambda ts: _time.strftime("%H:00", _time.localtime(ts))

    cutoff = now - window
    buckets: dict[str, dict] = {}
    per_model: dict[str, dict] = {}
    for c in calls:
        ts = c.get("ts", 0)
        if ts < cutoff:
            continue
        key = label(ts)
        b = buckets.setdefault(key, {"bucket": key, "calls": 0, "cost": 0.0})
        b["calls"] += 1
        b["cost"] = round(b["cost"] + c.get("cost", 0.0), 6)

        model = c.get("model", "?")
        m = per_model.setdefault(model, {"model": model, "calls": 0, "cost": 0.0})
        m["calls"] += 1
        m["cost"] = round(m["cost"] + c.get("cost", 0.0), 6)

    total_cost = round(sum(c.get("cost", 0.0) for c in calls if c.get("ts", 0) >= cutoff), 6)
    total_calls = sum(c["ts"] >= cutoff for c in calls)
    return {
        "period": period,
        "total_calls": total_calls,
        "total_cost": total_cost,
        "buckets": sorted(buckets.values(), key=lambda b: b["bucket"]),
        "per_model": sorted(per_model.values(), key=lambda m: -m["cost"]),
    }


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


async def _run_phase1_loop(idea: str, resume_idea_id: str | None = None,
                           resume_comment: str | None = None):
    from . import budget
    started = time.time()
    spent_at_start = budget.get_spent()
    await _broadcast("run_started", {"idea": idea})
    result = await run_orchestrator(idea, inbox=_inbox, resume_idea_id=resume_idea_id,
                                    resume_comment=resume_comment)
    if result.status == "needs_clarification":
        # Debate durably paused on a question — the orchestrator already
        # emitted run_paused; do NOT signal finished.
        return
    await _broadcast("run_finished", {
        "status": result.status,
        "verdict": result.verdict,
        "creative_angles": result.creative_angles,
        "has_prd": bool(result.prd),
        "prd": result.prd,
        "security_audit": result.security_audit,
        "turns_used": result.turns_used,
        "error": result.error,
        # Debate economics + duration, for the live UI.
        "cost": round(max(0.0, budget.get_spent() - spent_at_start), 4),
        "elapsed_seconds": round(time.time() - started, 1),
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

    result = get_run(run_id)
    if not result:
        raise HTTPException(404, f"No active run with id {run_id}")

    if decision in ("abort", "reject"):
        result.status = "stopped"
        store.set_status("stopped")
        store.log("Human", "user", f"Decision: {decision.upper()} — stopping.")
        await _broadcast("run_finished", {
            "status": result.status,
            "verdict": result.verdict,
            "creative_angles": result.creative_angles,
            "has_prd": bool(result.prd),
            "prd": result.prd,
            "security_audit": result.security_audit,
            "turns_used": result.turns_used,
            "error": result.error,
        })
        return {"status": "stopped", "run_id": run_id}

    if decision == "approve":
        result.status = "done"
        store.set_status("approved")
        store.log("Human", "user", "PRD APPROVED.")
        await _broadcast("run_finished", {
            "status": result.status,
            "verdict": result.verdict,
            "creative_angles": result.creative_angles,
            "has_prd": bool(result.prd),
            "prd": result.prd,
            "security_audit": result.security_audit,
            "turns_used": result.turns_used,
            "error": result.error,
        })
        return {"status": "approved", "run_id": run_id}

    if decision == "proceed":
        # Human says proceed anyway — queue steering and let the orchestrator continue
        if steering:
            _inbox.add_steering(steering)
        if urls:
            _inbox.add_urls(urls)
        store.log("Human", "user", "PROCEED — continuing orchestration.")
        return {"status": "continuing", "run_id": run_id}

    raise HTTPException(400, f"Unknown decision: {decision}")


@app.get("/api/paused")
async def api_paused(request: Request):
    auth.get_current_user(request)
    from .agents.orchestrator import _RUNS
    return {"paused_runs": list(_RUNS.keys())}


# ── Idea history + checkpoint persistence (IDEA_HISTORY_ADDENDUM) ────
_ITEMS_PER_PAGE = 10

# Statuses accepted on import (subset of the idea-tree lifecycle).
_VALID_EXPORT_STATUSES = {"ACTIVE", "PARK", "PRUNED"}


def _export_idea(idea: dict) -> dict:
    """Full-fidelity export shape for one idea: current state + every run."""
    scores = None
    try:
        scores = json.loads(idea.get("scores") or "null")
    except (json.JSONDecodeError, TypeError):
        pass
    return {
        "format": "venturebot-idea",
        "version": 1,
        "title": idea.get("title"),
        "pitch": idea.get("description"),
        "status": idea.get("status") or "ACTIVE",
        "verdict": idea.get("verdict"),
        "scores": scores,
        "research_brief": idea.get("research_brief"),
        "debate_transcript": idea.get("debate_transcript"),
        "prd_text": idea.get("prd_text"),
        "created_at": idea.get("created_at"),
        "updated_at": idea.get("updated_at"),
        # Full per-run history (transcripts, PRDs, comments) — the second brain.
        "runs": get_store().get_idea_runs(idea["id"], include_blobs=True),
    }


def _safe_filename(title: str) -> str:
    """Filesystem-safe slug from an idea title (for export filenames)."""
    keep = [c if c.isalnum() else "-" for c in title.lower().strip()]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return (slug.strip("-") or "idea")[:60]


def _json_download(payload: dict, filename: str) -> Response:
    """Serialize a JSON payload as an attachment download."""
    body = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _idea_to_item(idea: dict) -> dict:
    """Shape an idea_tree row like the portfolio Project type."""
    verdict = None
    try:
        scores = json.loads(idea.get("scores") or "{}")
        verdict = scores.get("verdict") or idea.get("verdict")
    except (json.JSONDecodeError, TypeError):
        verdict = idea.get("verdict")
    description = _auto_description(idea)
    tags = _idea_tags(idea)
    runs = get_store().get_idea_runs(idea["id"])
    return {
        "id": idea["id"],
        "title": idea["title"],
        "date": time.strftime("%Y-%m-%d", time.localtime(idea["created_at"])),
        "status": idea["status"],
        "scores": _scores_compact(idea.get("scores")),
        "description": description,
        # The human's full original pitch (edited text if it was edited).
        "pitch": idea.get("description") or None,
        "verdict": verdict,
        "github_url": None,
        "deployed_url": None,
        "linkedin_url": None,
        "tags": tags,
        "categories": tags[:3],
        "created_at": idea["created_at"],
        "updated_at": idea["updated_at"],
        "has_prd": bool(idea.get("prd_text")),
        "run_count": len(runs),
    }


def _scores_compact(scores_json: str | None) -> dict | None:
    if not scores_json:
        return None
    try:
        scores = json.loads(scores_json)
    except (json.JSONDecodeError, TypeError):
        return None
    out = {}
    for key in ("novelty", "feasibility", "market_fit"):
        s = scores.get(key)
        if isinstance(s, dict):
            out[key] = s.get("score")
        elif isinstance(s, (int, float)):
            out[key] = s
    return out or None


def _auto_description(idea: dict) -> str:
    status = idea.get("status")
    verdict = idea.get("verdict")
    if status == "PARK":
        return f"Parked. Verdict: {verdict or 'PARK'}. Awaiting human decision or revival."
    if status == "PRUNED":
        return f"Pruned. Verdict: {verdict or 'PRUNE'}."
    if idea.get("prd_text"):
        return f"Research + debate complete. PRD ready for approval."
    if idea.get("research_brief"):
        return f"Research complete. Verdict: {verdict or 'pending'}."
    return "Captured — debate not yet run."


def _idea_tags(idea: dict) -> list[str]:
    return extract_tags(
        idea.get("title"),
        idea.get("research_brief"),
        idea.get("debate_transcript"),
    )


@app.get("/api/ideas")
async def api_ideas(request: Request, page: int = 1, category: str | None = None,
                   date_year: int | None = None, date_month: int | None = None,
                   search: str | None = None, status: str | None = None):
    """Paginated, filterable list of ideas (A1)."""
    auth.get_current_user(request)
    ideas = _filter_ideas(category=category, date_year=date_year,
                          date_month=date_month, search=search, status=status)

    total = len(ideas)
    total_pages = max(1, -(-total // _ITEMS_PER_PAGE))  # ceil
    page = max(1, min(page, total_pages))
    start = (page - 1) * _ITEMS_PER_PAGE
    items = [_idea_to_item(i) for i in ideas[start:start + _ITEMS_PER_PAGE]]
    return {"items": items, "total": total, "page": page, "total_pages": total_pages}


def _filter_ideas(*, category: str | None = None, date_year: int | None = None,
                  date_month: int | None = None, search: str | None = None,
                  status: str | None = None) -> list[dict]:
    """Apply the A1 query filters to the full idea_tree and return matches."""
    ideas = get_store().get_idea_tree()
    if status:
        ideas = [i for i in ideas if (i.get("status") or "").upper() == status.upper()]
    if date_year:
        ideas = [i for i in ideas if time.localtime(i["created_at"]).tm_year == date_year]
    if date_month:
        ideas = [i for i in ideas if time.localtime(i["created_at"]).tm_mon == date_month]
    if category:
        ideas = [i for i in ideas if category in _idea_tags(i)]
    if search:
        q = search.lower()
        ideas = [
            i for i in ideas
            if q in (i.get("title") or "").lower() or q in _auto_description(i).lower()
        ]
    return ideas


@app.get("/api/ideas/facets")
async def api_ideas_facets(request: Request):
    """Sidebar facets: tag counts, date tree, status counts (F2, F3, F4)."""
    auth.get_current_user(request)
    ideas = get_store().get_idea_tree()

    # Tag counts (across all ideas, unfiltered).
    tag_counts: dict[str, int] = {}
    for idea in ideas:
        for tag in _idea_tags(idea):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    # Date tree: year -> month -> count.
    years: dict[int, dict[int, int]] = {}
    for idea in ideas:
        lt = time.localtime(idea["created_at"])
        years.setdefault(lt.tm_year, {}).setdefault(lt.tm_mon, 0)
        years[lt.tm_year][lt.tm_mon] += 1

    # Status counts.
    status_counts: dict[str, int] = {}
    for idea in ideas:
        st = idea.get("status") or "ACTIVE"
        status_counts[st] = status_counts.get(st, 0) + 1

    return {
        "tags": [{"tag": t, "count": c} for t, c in sorted(tag_counts.items(), key=lambda kv: -kv[1])],
        "years": [
            {
                "year": y,
                "months": [
                    {"month": m, "count": c}
                    for m, c in sorted(months.items(), key=lambda kv: -kv[0])
                ],
            }
            for y, months in sorted(years.items(), key=lambda kv: -kv[0])
        ],
        "statuses": [
            {"status": s, "count": c}
            for s, c in sorted(status_counts.items(), key=lambda kv: -kv[1])
        ],
    }


@app.get("/api/ideas/csv")
async def api_ideas_csv(request: Request, category: str | None = None,
                       date_year: int | None = None, date_month: int | None = None,
                       search: str | None = None, status: str | None = None):
    """CSV export of the (filtered) idea list (F9)."""
    auth.get_current_user(request)
    import csv
    import io

    ideas = _filter_ideas(category=category, date_year=date_year,
                          date_month=date_month, search=search, status=status)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "title", "date", "status", "verdict",
                     "novelty", "feasibility", "market_fit", "tags", "description"])
    for idea in ideas:
        item = _idea_to_item(idea)
        scores = item.get("scores") or {}
        writer.writerow([
            item["id"], item["title"], item["date"], item["status"],
            item["verdict"] or "",
            scores.get("novelty", ""), scores.get("feasibility", ""),
            scores.get("market_fit", ""),
            ";".join(item["tags"]), item["description"],
        ])
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="venturebot-ideas.csv"'},
    )


@app.get("/api/ideas/export")
async def api_ideas_export_all(request: Request):
    """Export ALL ideas + full run history as one JSON backup file.
    Declared before /api/ideas/{idea_id} so 'export' is not captured as an id."""
    auth.get_current_user(request)
    ideas = get_store().get_idea_tree()
    bundle = {
        "format": "venturebot-ideas-backup",
        "version": 1,
        "exported_at": time.time(),
        "ideas": [_export_idea(i) for i in ideas],
    }
    return _json_download(bundle, "venturebot-ideas-backup.json")


@app.post("/api/ideas/import")
async def api_ideas_import(request: Request):
    """Import ideas from a JSON export (single-idea export or full backup).
    Every imported idea gets fresh IDs; run history is preserved inside the
    idea. Duplicate titles are kept (import is additive by design)."""
    auth.get_current_user(request)
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "body is not valid JSON")

    if isinstance(data, dict) and data.get("format") == "venturebot-ideas-backup":
        ideas = data.get("ideas") or []
    elif isinstance(data, dict) and data.get("format") == "venturebot-idea":
        ideas = [data]
    else:
        raise HTTPException(400, "unrecognized format — expected a VentureBot idea export")

    s = get_store()
    imported: list[dict] = []
    for raw in ideas:
        if not isinstance(raw, dict) or not raw.get("title"):
            raise HTTPException(400, "each idea needs at least a 'title'")
        new_id = s.create_idea(str(raw["title"])[:200], description=raw.get("pitch"))
        s.update_idea_content(
            new_id,
            research_brief=raw.get("research_brief"),
            debate_transcript=raw.get("debate_transcript"),
            prd_text=raw.get("prd_text"),
            verdict=raw.get("verdict"),
        )
        if raw.get("scores"):
            try:
                scores = raw["scores"]
                if isinstance(scores, str):
                    scores = json.loads(scores)
                s.update_idea_scores(new_id, scores)
            except Exception:
                pass
        status = str(raw.get("status") or "ACTIVE").upper()
        if status in _VALID_EXPORT_STATUSES and status != "ACTIVE":
            s.update_idea_status(new_id, status, "imported")
        # Restore run history (new run-row ids, same numbers/comments).
        runs = raw.get("runs") or []
        if isinstance(runs, list):
            for r in sorted(runs, key=lambda x: x.get("run_number") or 0):
                run_row = s.start_idea_run(new_id, comment=r.get("comment"))
                r_scores = r.get("scores")
                if isinstance(r_scores, str):
                    try:
                        r_scores = json.loads(r_scores)
                    except Exception:
                        r_scores = None
                s.finish_idea_run(
                    run_row,
                    status=str(r.get("status") or "done"),
                    verdict=r.get("verdict"),
                    scores=r_scores if isinstance(r_scores, (dict, list)) else None,
                    research_brief=r.get("research_brief"),
                    debate_transcript=r.get("debate_transcript"),
                    prd_text=r.get("prd_text"),
                    turns_used=r.get("turns_used"),
                )
        imported.append({"id": new_id, "title": raw["title"][:200],
                         "runs_restored": len(raw.get("runs") or [])})
    await _broadcast("ideas_imported", {"count": len(imported)})
    return {"status": "imported", "count": len(imported), "ideas": imported}


@app.get("/api/ideas/{idea_id}")
async def api_idea_detail(idea_id: str, request: Request):
    """Full idea detail (A2): PRD, transcript, scores."""
    auth.get_current_user(request)
    idea = get_store().get_idea(idea_id)
    if not idea:
        raise HTTPException(404, "idea not found")
    item = _idea_to_item(idea)
    item["prd_text"] = idea.get("prd_text")
    item["research_brief"] = idea.get("research_brief")
    item["debate_transcript"] = idea.get("debate_transcript")
    item["workspace_path"] = idea.get("workspace_path")
    item["human_intervention_count"] = idea.get("human_intervention_count")
    item["runs"] = get_store().get_idea_runs(idea_id)
    return item


@app.get("/api/ideas/{idea_id}/export")
async def api_idea_export(idea_id: str, request: Request):
    """Export one idea + its full run history as a downloadable JSON file."""
    auth.get_current_user(request)
    idea = get_store().get_idea(idea_id)
    if not idea:
        raise HTTPException(404, "idea not found")
    return _json_download(_export_idea(idea),
                          f"venturebot-idea-{_safe_filename(idea['title'])}.json")


@app.get("/api/ideas/{idea_id}/runs")
async def api_idea_runs(idea_id: str, request: Request):
    """Run history for an idea (summaries, no heavy blobs)."""
    auth.get_current_user(request)
    if not get_store().get_idea(idea_id):
        raise HTTPException(404, "idea not found")
    return {"runs": get_store().get_idea_runs(idea_id)}


@app.get("/api/ideas/{idea_id}/runs/{run_id}")
async def api_idea_run_detail(idea_id: str, run_id: str, request: Request):
    """Full detail of one past debate run: transcript events, PRD, brief,
    verdict, the human comment that started it — everything needed to replay
    the debate exactly as it looked while it was running."""
    auth.get_current_user(request)
    run = get_store().get_idea_run(run_id)
    if not run or run["idea_id"] != idea_id:
        raise HTTPException(404, "run not found")
    events: list[dict] = []
    if run.get("debate_transcript"):
        try:
            parsed = json.loads(run["debate_transcript"])
            if isinstance(parsed, list):
                events = parsed
        except Exception:
            pass  # legacy/corrupt transcript → surface as empty, not a crash
    return {
        "id": run["id"],
        "idea_id": run["idea_id"],
        "run_number": run["run_number"],
        "status": run["status"],
        "verdict": run.get("verdict"),
        "scores": run.get("scores"),
        "comment": run.get("comment"),
        "turns_used": run.get("turns_used"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "events": events,
        "prd_text": run.get("prd_text"),
        "research_brief": run.get("research_brief"),
    }


@app.post("/api/ideas/{idea_id}/edit")
async def api_idea_edit(idea_id: str, request: Request):
    """Human edit of an idea's title and/or full pitch text.

    The pitch is what a resumed debate receives as its input, so editing it is
    how the human steers an idea between runs."""
    auth.get_current_user(request)
    data = await request.json()
    title = (data.get("title") or "").strip() or None
    pitch = (data.get("pitch") or "").strip() or None
    if not title and not pitch:
        raise HTTPException(400, "nothing to update")
    ok = get_store().update_idea_text(idea_id, title=title, description=pitch)
    if not ok:
        raise HTTPException(404, "idea not found")
    await _broadcast("idea_updated", {"idea_id": idea_id})
    return {"status": "updated", "idea_id": idea_id}


@app.post("/api/ideas/{idea_id}/archive")
async def api_idea_archive(idea_id: str, request: Request):
    """Park an idea (A4)."""
    auth.get_current_user(request)
    idea = get_store().get_idea(idea_id)
    if not idea:
        raise HTTPException(404, "idea not found")
    get_store().update_idea_status(idea_id, "PARK", "archived by human")
    return {"status": "ok", "idea_id": idea_id}


@app.delete("/api/ideas/{idea_id}")
async def api_idea_delete(idea_id: str, request: Request):
    """Hard-delete an idea (UI_UX_NOTES #5). Refused while it is running."""
    auth.get_current_user(request)
    idea = get_store().get_idea(idea_id)
    if not idea:
        raise HTTPException(404, "idea not found")
    state = store.load_state()
    if state.get("status") == "running":
        raise HTTPException(409, "cannot delete an idea while a debate is running")
    get_store().delete_idea(idea_id)
    await _broadcast("idea_deleted", {"idea_id": idea_id})
    return {"status": "deleted", "idea_id": idea_id}


@app.post("/api/ideas/duplicate-check")
async def api_idea_duplicate_check(request: Request):
    """Cheap token-overlap duplicate check before submitting (UI_UX_NOTES #4)."""
    auth.get_current_user(request)
    data = await request.json()
    title = data.get("title", "").strip()
    if not title:
        return {"duplicates": []}
    matches = get_store().find_similar_ideas(title, limit=3)
    return {
        "duplicates": [
            {"id": m["id"], "title": m["title"], "status": m["status"]}
            for m in matches
        ],
    }


@app.post("/api/ideas/{idea_id}/resume")
async def api_idea_resume(idea_id: str, request: Request):
    """Resume an idea with full previous context (P0.5).
    
    Loads all previous context (research brief, debate transcript, verdict, PRD)
    and starts a new orchestrator run that continues from where it left off.
    Accepts an optional human comment ("what changed / what I want next") that
    is injected into the first turn and recorded as part of the new run's history.
    """
    auth.get_current_user(request)
    idea = get_store().get_idea(idea_id)
    if not idea:
        raise HTTPException(404, "idea not found")
    
    # Check if a run is already in progress
    state = store.load_state()
    if state.get("status") == "running":
        raise HTTPException(409, "a debate is already running")
    
    try:
        body = await request.json()
    except Exception:
        body = {}
    comment = (body.get("comment") or "").strip() or None
    
    # Start a new run with the previous context + the human's new comment.
    # Resume from the FULL pitch text (edited version wins), not the truncated title.
    full_text = (idea.get("description") or "").strip() or idea["title"]
    asyncio.create_task(_run_phase1_loop(full_text, resume_idea_id=idea_id,
                                         resume_comment=comment))
    await _broadcast("idea_resumed", {"idea_id": idea_id, "title": idea["title"],
                                      "has_comment": bool(comment)})
    return {"status": "resumed", "idea_id": idea_id, "title": idea["title"]}


@app.get("/api/checkpoints")
async def api_checkpoints(request: Request):
    """List in-progress runs from the orchestrator (A5)."""
    auth.get_current_user(request)
    from .agents.orchestrator import _RUNS
    checkpoints = []
    for rid, r in _RUNS.items():
        checkpoints.append({
            "run_id": rid,
            "idea": r.idea[:200],
            "phase": "orchestrator",
            "status": r.status,
            "saved_at": time.time(),
        })
    return {"checkpoints": checkpoints}


@app.post("/api/clarify/answer")
async def api_clarify_answer(request: Request):
    """Answer a paused clarification and resume the debate from disk.

    Works no matter how much time has passed — even after server restarts —
    because the pause snapshot is durable (data/paused_runs/{run_id}.json).
    """
    auth.get_current_user(request)
    data = await request.json()
    run_id = data.get("run_id", "").strip()
    answer = data.get("answer", "").strip()
    if not run_id or not answer:
        raise HTTPException(400, "run_id and answer are required")

    pause = answer_clarify(run_id, answer)  # pops the durable snapshot
    if not pause:
        raise HTTPException(404, f"No pending clarification for run {run_id}")
    state = store.load_state()
    if state.get("status") == "running":
        # Another debate took over while this one was waiting — put the
        # snapshot back so it isn't lost.
        from .agents.orchestrator import write_pause
        write_pause(pause)
        raise HTTPException(409, "another debate is currently running — answer it after it finishes")

    store.log("Human", "user", f"Clarify answer: {answer[:200]}")

    async def _resume():
        result = await run_orchestrator(
            pause.get("idea") or "",
            paused_state=pause,
            clarify_answer=answer,
        )
        await _broadcast("run_finished", {
            "status": result.status,
            "verdict": result.verdict,
            "creative_angles": result.creative_angles,
            "has_prd": bool(result.prd),
            "prd": result.prd,
            "security_audit": result.security_audit,
            "turns_used": result.turns_used,
            "error": result.error,
        })

    asyncio.create_task(_resume())
    await _broadcast("clarify_answered", {"run_id": run_id})
    return {"status": "resumed", "resumed_run_id": run_id}


# ── Self-improvement (M3) ────────────────────────────────────────────
@app.post("/api/feedback")
async def api_feedback(request: Request):
    """Human feedback → lesson pipeline. When the user corrects the agent,
    this captures the correction as a durable lesson that future runs will read."""
    auth.get_current_user(request)
    data = await request.json()
    feedback = data.get("feedback", "").strip()
    run_id = data.get("run_id", "").strip()
    if not feedback:
        raise HTTPException(400, "feedback is required")
    # Save as a lesson that load_memories() will return on next run
    s = get_store()
    try:
        s.save_lesson(
            f"human_feedback_{run_id or 'manual'}",
            f"Human correction: {feedback}",
            "human_feedback"
        )
        store.log("System", "core", f"Human feedback saved as lesson: {feedback[:200]}")
        await _broadcast("feedback_saved", {"feedback": feedback[:200]})
        return {"status": "saved"}
    except Exception as e:
        raise HTTPException(500, str(e))

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
    # The SPA shell changes with every deploy — never let browsers cache it.
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
