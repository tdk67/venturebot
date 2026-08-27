"""VentureBot API — rewritten near-stateless backend (T1).

Contract (REWRITE_PLAN.md Part C, T1):
  POST   /api/debates                 create a run   → 201 {run_id, status}
  GET    /api/debates/{run_id}        run status
  GET    /api/debates/{run_id}/events SSE event stream (per-run, D4)
  GET    /api/debates/{run_id}/result 200 result / 202 not-ready / 410 gone (S7)
  POST   /api/debates/{run_id}/result/ack   client confirms download (S7)
  POST   /api/debates/{run_id}/clarify      answer a clarifying question
  POST   /api/byok/verify             validate a user-provided key (format only)
  GET    /api/health                  liveness

Design (locked decisions):
  * D1 — BYOK only. api_key is REQUIRED per request; no server key, no fallback.
  * D2 — server persists only in-flight run records until ACK/TTL.
  * D3 — result held server-side until client ACK (no mid-flight resume in v1).
  * D4 — events are per-run; the legacy global broadcast is deleted.

T1 is the API SKELETON: this module holds an in-memory run registry and the
route contract only. The real orchestrator (T2/T3), rate limits (T4) and the
ephemeral TTL/ACK store (T5) plug in behind the same contracts in later tasks.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# -- Security headers (G1-G3) ----------------------------------------------
# Strict CSP: all scripts same-origin; no third-party scripts, no eval.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

app = FastAPI(title="VentureBot API", version="0.2.0")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = _CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    return response


# -- Run registry (in-memory; T5 replaces this with the TTL store) ---------

_VALID_STATUSES = {"queued", "running", "failed", "done"}

# Recognized BYOK key formats (OpenRouter / Gemini). Verification is
# format-only here — a network check lives in a later task (T3) so this
# skeleton never makes outbound calls.
_KEY_PATTERNS = [
    re.compile(r"^sk-or-v1-[A-Za-z0-9_-]{16,}$"),  # OpenRouter
    re.compile(r"^AIza[A-Za-z0-9_-]{20,}$"),       # Google AI Studio
]


@dataclass
class RunRecord:
    run_id: str
    idea: str
    api_key: str  # held in memory only for this run's lifetime (D1/S2)
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    events: list[dict] = field(default_factory=list)
    result: dict | None = None
    acked: bool = False
    error: str | None = None


_RUNS: dict[str, RunRecord] = {}


def _emit(run: RunRecord, event: str, data: dict) -> None:
    """Append a per-run event. Payload is JSON-safe; api_key is never included."""
    run.events.append({"event": event, "data": data, "ts": time.time()})


def _lookup(run_id: str) -> RunRecord:
    rec = _RUNS.get(run_id)
    if rec is None:
        # S3: unknown run ids are indistinguishable from nonexistent → 404
        raise HTTPException(404, "not found")
    return rec


def _require_api_key(body: dict) -> str:
    key = (body.get("api_key") or "").strip()
    if not key:
        # D1: BYOK is REQUIRED — no server-key fallback exists in any form.
        raise HTTPException(400, "api_key required")
    return key


# -- New API surface -------------------------------------------------------

@app.get("/api/health")
async def api_health():
    return {"status": "ok"}


@app.post("/api/debates", status_code=201)
async def api_create_debate(request: Request):
    body = await request.json()
    idea = (body.get("idea") or "").strip()
    if not idea:
        raise HTTPException(400, "idea is required")
    api_key = _require_api_key(body)
    urls = body.get("urls") or []

    run_id = str(uuid.uuid4())  # 122-bit unguessable (S3)
    rec = RunRecord(run_id=run_id, idea=idea, api_key=api_key)
    _emit(rec, "run_created", {"idea_len": len(idea), "urls": len(urls)})
    _RUNS[run_id] = rec

    # T2 wires the real orchestrator here (async task per run).
    return {"run_id": run_id, "status": rec.status}


@app.get("/api/debates/{run_id}")
async def api_get_debate(run_id: str):
    rec = _lookup(run_id)
    return {
        "run_id": rec.run_id,
        "status": rec.status,
        "created_at": rec.created_at,
        "error": rec.error,
    }


@app.get("/api/debates/{run_id}/events")
async def api_debate_events(run_id: str, request: Request):
    # Validate existence first: unknown ids → 404 (never an open stream).
    rec = _lookup(run_id)

    async def gen():
        # hello frame + replay of already-emitted events (D3: reload re-polls)
        yield _sse("hello", {"run_id": run_id, "status": rec.status})
        seen = 0
        while True:
            if await request.is_disconnected():
                break
            # replay newly appended events since last check (queued runs never
            # emit after run_created, so a queued run yields hello then pings)
            fresh = rec.events[seen:]
            seen = len(rec.events)
            for ev in fresh:
                yield _sse(ev["event"], ev["data"])
            yield _sse("ping", {"t": int(time.time())})
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/api/debates/{run_id}/result")
async def api_get_result(run_id: str):
    rec = _lookup(run_id)
    if rec.acked or (rec.status == "done" and rec.result is None and rec.acked):
        raise HTTPException(410, "result gone")  # S7: after ACK, gone
    if rec.result is None:
        raise HTTPException(202, "not ready")  # S7: not ready yet
    return {"run_id": rec.run_id, "result": rec.result}


@app.post("/api/debates/{run_id}/result/ack")
async def api_ack_result(run_id: str):
    rec = _lookup(run_id)
    if rec.result is None:
        raise HTTPException(409, "result not ready")
    rec.acked = True
    _emit(rec, "result_acked", {"run_id": run_id})
    return {"status": "acked", "run_id": run_id}


@app.post("/api/debates/{run_id}/clarify")
async def api_clarify(run_id: str, request: Request):
    rec = _lookup(run_id)
    body = await request.json()
    answer = (body.get("answer") or "").strip()
    if not answer:
        raise HTTPException(400, "answer is required")
    # T2 wires the answer into a paused run; the skeleton records it.
    _emit(rec, "clarify_received", {"answer_len": len(answer)})
    return {"status": "queued", "run_id": run_id}


@app.post("/api/byok/verify")
async def api_byok_verify(request: Request):
    body = await request.json()
    key = (body.get("api_key") or "").strip()
    if not key:
        raise HTTPException(400, "api_key required")
    ok = any(p.match(key) for p in _KEY_PATTERNS)
    provider = None
    if key.startswith("sk-or-"):
        provider = "openrouter"
    elif key.startswith("AIza"):
        provider = "gemini"
    return {"valid": ok, "provider": provider}


# -- Static assets + pages -------------------------------------------------

app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parent.parent / "static")),
    name="static",
)


@app.get("/", response_class=HTMLResponse)
async def landing_page():
    html = (Path(__file__).resolve().parent.parent / "templates" / "landing.html").read_text()
    return HTMLResponse(html, headers={"Cache-Control": "public, max-age=300"})


@app.get("/app", response_class=HTMLResponse)
async def dashboard():
    html = (Path(__file__).resolve().parent.parent / "templates" / "index.html").read_text()
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
