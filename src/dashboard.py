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
import contextlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .rate_limit import (
    MAX_BODY_BYTES,
    accept_run_created,
    check_hourly,
    client_ip,
    has_active_run,
    sse_acquire,
    sse_release,
)
from .ephemeral_store import EphemeralStore
from .inflight_sweeper import sweep_workspaces

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

# T5 — periodic TTL / workspace sweeper. Every SWEEP_INTERVAL seconds we drop
# expired runs from the ephemeral store and wipe their workspaces, so idea
# text, research and PRD content never linger on disk past the TTL (S6/D2).
# The store's sweep_ttl() is the tested primitive; this loop just drives it.
# Run only under the ASGI lifespan (uvicorn / TestClient-with-context); a plain
# TestClient() never starts it, which is why the TTL tests are deterministic.
SWEEP_INTERVAL_SECONDS = 60

try:
    from contextlib import asynccontextmanager


    @asynccontextmanager
    async def lifespan(_app):
        async def _tick():
            while True:
                await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
                try:
                    _sweep_once()
                except Exception:
                    pass

        task = asyncio.create_task(_tick())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app.router.lifespan_context = lifespan
except Exception:
    # A plain TestClient() with no lifespan support is fine for tests.
    pass


def _sweep_once() -> list[str]:
    """Tick the store + workspace sweepers once. Returns the run_ids swept.
    Deterministic and synchronous, so tests call it directly; the lifespan loop
    calls this repeatedly on its own interval.

    Order matters: `STORE.sweep_ttl()` first fires the `expired` watcher for
    each dropped run (so any still-open SSE sees it), and only then do we drop
    the run's remaining in-memory `_RUNS` record and its workspace, so the
    server holds nothing after expiry (S6/D2).
    """
    swept = STORE.sweep_ttl()
    for run_id in swept:
        _RUNS.pop(run_id, None)
        try:
            sweep_workspaces({run_id})
        except Exception:
            pass
    return swept


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = _CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    return response


# -- Run registry (in-memory; live in the ephemeral TTL+ACK store) ---------

_VALID_STATUSES = {"queued", "running", "failed", "done"}

# Recognized BYOK key formats (OpenRouter / Gemini). Verification is
# format-only here — a network check lives in a later task (T3) so this
# skeleton never makes outbound calls.
_KEY_PATTERNS = [
    re.compile(r"^sk-or-v1-[A-Za-z0-9_-]{16,}$"),  # OpenRouter
    re.compile(r"^AIza[A-Za-z0-9_-]{20,}$"),       # Google AI Studio
]


def _redact(text: str, api_key: str) -> str:
    """S2/OPSEC: strip a user-provided API key from any text (errors, events).

    Returns the text unchanged when the key is empty or absent, otherwise with
    every occurrence replaced by the literal `[REDACTED]`.
    """
    if not text or not api_key:
        return text
    return text.replace(api_key, "[REDACTED]")


@dataclass
class RunRecord:
    run_id: str
    idea: str
    api_key: str = ""  # held in memory ONLY for this run's lifetime (D1/S2)
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    events: list[dict] = field(default_factory=list)
    result: dict | None = None
    acked: bool = False
    error: str | None = None


_RUNS: dict[str, RunRecord] = {}

# T5: ephemeral TTL + ACK store (D2/D3/S7). `_RUNS` above remains as a legacy
# alias for the raw map only where the SSE route still needs the full record
# for replay; new result/ack routes read from the store so lifecycle (TTL,
# ACK -> 410 gone) is enforced in one place. `_emit_plugin(run_id, event, data)`
# is a fire-and-forget notification the store fires when a run is dropped by TTL.


def _emit_plugin(run_id: str, event: str, data: dict) -> None:
    """Called by the ephemeral store when a run is dropped by TTL. Best-effort:
    appends an `expired` transient event to the run's record if it still exists
    (so any open SSE stream sees it) and nudges the workspace sweeper."""
    rec = _RUNS.get(run_id)
    if rec is not None:
        try:
            rec.events.append({"event": event, "data": data, "ts": time.time()})
        except Exception:
            pass


# Module-level store all routes share. Tests swap this for a fresh store.
STORE = EphemeralStore(watch_plugin=_emit_plugin)


def _emit(run: RunRecord, event: str, data: dict) -> None:
    """Append a per-run event. Payload is JSON-safe; api_key is never included."""
    run.events.append({"event": event, "data": data, "ts": time.time()})


async def _read_body_limited(request: Request):
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(413, "request too large")
    return body


def _lookup(run_id: str) -> RunRecord:
    # T5: read from the ephemeral store. Gone (acked/expired) -> 410, unknown
    # -> 404 (S3).
    rec = STORE.get(run_id)
    if rec is None:
        if STORE.is_gone(run_id):
            raise HTTPException(410, "result gone")
        raise HTTPException(404, "not found")
    return rec


def _require_api_key(body: dict) -> str:
    key = (body.get("api_key") or "").strip()
    if not key:
        # D1: BYOK is REQUIRED — no server-key fallback exists in any form.
        raise HTTPException(400, "api_key required")
    return key


# -- BYOK plumbing (T3) -----------------------------------------------------
# D1: keys are per-request, live ONLY in memory for that run's lifetime, and
# are passed through to the orchestrator's factory. There is no stored server
# key and no fallback. The key is scrubbed from the in-memory record as soon as
# the debate ends (finally), and every surfaced string (error, events) is
# redacted.


def _orchestrator(idea: str, *, api_key: str | None, external_run_id: str | None, urls: list[str] | None = None, **kwargs):
    """Thin wrapper so tests can monkeypatch the module-level symbol."""
    from .agents.orchestrator import run_orchestrator
    from .steering import SteeringInbox

    inbox = SteeringInbox()
    if urls:
        inbox.add_urls(urls)
    return run_orchestrator(
        idea,
        api_key=api_key,
        external_run_id=external_run_id,
        inbox=inbox,
        **kwargs,
    )


def _is_intermittent_error(exc: Exception) -> bool:
    """Classify whether an exception is a transient network/server error suitable for retry."""
    msg = str(exc).lower()
    # Fatal / Client / Auth errors — never retry
    if any(k in msg for k in ("api key", "401", "403", "permission", "unauthorized", "404", "not found", "400", "invalid argument", "input blocked")):
        return False
    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
        return False
    # Transient network / rate-limit / server errors — retryable
    return any(k in msg for k in ("429", "resource exhausted", "rate limit", "quota", "500", "502", "503", "504", "unavailable", "overloaded", "timeout", "timed out", "connect", "connection reset", "econnreset"))


async def _run_debate(rec: RunRecord, api_key: str, *, idea: str | None = None, urls: list[str] | None = None) -> None:
    """Drive ONE debate for a created run (T3).

    Responsibilities:
      * status running
      * pass idea+per-run key to the orchestrator (BYOK, memory only)
      * retry intermittent errors up to 3 times with exponential backoff
      * store the result for the /result + ack contract (S7)
      * emit run_finished / run_failed (loud failures, T2)
      * DISCARD the key in `finally` so it is never reused or retained
      * redact the key from any error/event text
    """
    async def _on_event(event: str, data: dict):
        _emit(rec, event, data)

    from .events import register_run_sink, unregister_run_sink
    register_run_sink(rec.run_id, _on_event)

    try:
        rec.status = "running"
        _emit(rec, "run_started", {"run_id": rec.run_id})

        result = None
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                if attempt > 1:
                    _emit(rec, "agent_turn", {
                        "agent": "System",
                        "text": f"⚠️ Intermittent network/API issue detected. Retrying attempt {attempt}/{max_retries}...",
                        "run_id": rec.run_id,
                    })
                result = await _orchestrator(
                    rec.idea if idea is None else idea,
                    api_key=api_key,
                    external_run_id=rec.run_id,
                    urls=urls,
                )
                if getattr(result, "status", "done") == "failed" and getattr(result, "error", None):
                    err_str = str(result.error).lower()
                    if attempt < max_retries and any(kw in err_str for kw in ("429", "resource exhausted", "rate limit", "503", "unavailable", "overloaded", "timeout", "timed out")):
                        await asyncio.sleep(2 ** (attempt - 1))
                        continue
                break
            except Exception as e:
                if attempt < max_retries and _is_intermittent_error(e):
                    await asyncio.sleep(2 ** (attempt - 1))
                    continue
                raise

        rec.status = getattr(result, "status", "done")
        if rec.status not in ("failed", "stopped"):
            rec.result = {
                "run_id": rec.run_id,
                "status": rec.status,
                "verdict": getattr(result, "verdict", None),
                "verdict_text": getattr(result, "verdict_text", None),
                "prd": getattr(result, "prd", None),
                "research_brief": getattr(result, "research_brief", None),
                "advocate_argument": getattr(result, "advocate_argument", None),
                "critic_rebuttal": getattr(result, "critic_rebuttal", None),
                "creative_angles": getattr(result, "creative_angles", None),
                "security_audit": getattr(result, "security_audit", None),
                "turns_used": getattr(result, "turns_used", 0),
                "clarification_question": getattr(result, "clarification_question", None),
                "transcript": getattr(result, "events", []),
            }
            _emit(rec, "run_finished", {"run_id": rec.run_id, "status": rec.status})
        else:
            if getattr(result, "error", None):
                rec.error = _redact(str(result.error), api_key)
            error_reason = rec.error or "Debate execution failed"
            _emit(rec, "run_failed", {"reason": error_reason, "run_id": rec.run_id})
    except asyncio.CancelledError:
        rec.status = "failed"
        rec.error = "run cancelled"
        _emit(rec, "run_failed", {"reason": "run cancelled", "run_id": rec.run_id})
    except Exception as e:
        # Loud failure (S2/T2): surface an explicit reason, key redacted.
        rec.status = "failed"
        rec.error = _redact(f"{type(e).__name__}: {e}", api_key)
        _emit(rec, "run_failed", {"reason": rec.error, "run_id": rec.run_id})
    finally:
        unregister_run_sink(rec.run_id)
        # D1/S2: the key never outlives its run. Stored/event/error text that
        # might have captured it is scrubbed too, and the record is cleared.
        _scrub_key_from_run(rec, api_key)
        rec.api_key = ""


def _scrub_key_from_run(rec: RunRecord, api_key: str) -> None:
    """Remove the key from any stored/event/error text in the run record."""
    if not api_key:
        return
    if rec.error:
        rec.error = _redact(rec.error, api_key)
    if rec.result:
        rec.result = _redact_dict(rec.result, api_key)
    for ev in rec.events:
        d = ev.get("data", {})
        for k, v in list(d.items()):
            if isinstance(v, str):
                d[k] = _redact(v, api_key)


def _redact_dict(d: dict, api_key: str) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out[k] = _redact_dict(v, api_key)
        elif isinstance(v, list):
            out[k] = [
                _redact_dict(i, api_key) if isinstance(i, dict)
                else (_redact(i, api_key) if isinstance(i, str) else i)
                for i in v
            ]
        elif isinstance(v, str):
            out[k] = _redact(v, api_key)
        else:
            out[k] = v
    return out


# -- New API surface -------------------------------------------------------

@app.get("/api/health")
async def api_health():
    return {"status": "ok"}


@app.post("/api/debates", status_code=201)
async def api_create_debate(request: Request):
    raw = await _read_body_limited(request)
    body = json.loads(raw or b"{}")
    idea = (body.get("idea") or "").strip()
    if not idea:
        raise HTTPException(400, "idea is required")
    api_key = _require_api_key(body)
    urls = body.get("urls") or []

    run_id = str(uuid.uuid4())  # 122-bit unguessable (S3)

    # T4 — S1: per-IP limits BEFORE a run is created.
    #   * hourly anti-flood credit is consumed at creation
    #   * concurrency: a 2nd run while this IP already EXECUTES one is rejected
    #     (the executing slot itself is taken by the executor, seam `begin_*`)
    ip = client_ip(request)
    if has_active_run(ip):
        raise HTTPException(429, "rate limit: too_many_concurrent")
    ok, reason = check_hourly(ip)
    if not ok:
        raise HTTPException(429, f"rate limit: {reason}")
    accept_run_created(ip)

    rec = RunRecord(run_id=run_id, idea=idea, api_key=api_key)
    _emit(rec, "run_created", {"idea_len": len(idea), "urls": len(urls)})
    _RUNS[run_id] = rec
    # T5: register in the ephemeral TTL + ACK store (D2/D3) so the result
    # lifecycle (ACK -> 410 gone, TTL sweeper) is enforced.
    STORE.register(rec)

    # Launch background debate execution
    asyncio.create_task(_run_debate(rec, api_key, urls=urls))

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

    # T4 — S10: cap concurrent SSE connections per IP.
    ip = client_ip(request)
    tok = sse_acquire(ip)
    if tok is None:
        raise HTTPException(429, "rate limit: too many connections")

    async def gen():
        try:
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
        finally:
            sse_release(ip, tok)

    return StreamingResponse(gen(), media_type="text/event-stream")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/api/debates/{run_id}/result")
async def api_get_result(run_id: str):
    rec = _lookup(run_id)
    if rec.result is None:
        raise HTTPException(202, "not ready")  # S7: not ready yet
    return {"run_id": rec.run_id, "result": rec.result}


@app.post("/api/debates/{run_id}/result/ack")
async def api_ack_result(run_id: str):
    # T5: ack through the store so the record is wiped + 410-tombstoned. If the
    # run doesn't have a result yet (still in flight / never finished), ACK is
    # rejected (409) and the record is left untouched.
    rec = STORE.get(run_id)
    if rec is None:
        if STORE.is_gone(run_id):
            # already gone -> treat as idempotent ack
            return {"status": "acked", "run_id": run_id}
        raise HTTPException(404, "not found")
    if rec.result is None:
        raise HTTPException(409, "result not ready")
    STORE.ack(run_id)
    # also drop the per-run workspace so no idea text lingers on disk (S6)
    try:
        sweep_workspaces({run_id})
    except Exception:
        pass
    _RUNS.pop(run_id, None)
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
    html = (Path(__file__).resolve().parent.parent / "templates" / "landing.html").read_text(encoding="utf-8")
    return HTMLResponse(html, headers={"Cache-Control": "public, max-age=300"})


@app.get("/app", response_class=HTMLResponse)
async def dashboard():
    html = (Path(__file__).resolve().parent.parent / "templates" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
