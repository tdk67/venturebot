"""T4 — Per-IP rate limits & caps (REWRITE_PLAN.md Part A: S1 + S10).

All counters are in-memory and per-process (resetting on restart), never
persisted, never logged. They key on the effective client IP, which is the
first hop of `X-Forwarded-For` (set by nginx / Cloud Run) and which falls
back to the peer address otherwise.

Limits enforced (S1 / S10):
  * max 1 concurrent EXECUTING run per IP   -> 429: the create route rejects a
    2nd create while this IP already has an executing run
  * max MAX_RUNS_PER_HOUR run-creations per IP hour -> 429 (anti-flood)
  * max request body 32 KB                  -> 413  (enforced in dashboard.py)
  * max 3 concurrent SSE connections / IP   -> 429 on a 4th live event stream

The hourly credit is consumed at create time. The concurrency cap governs
runs that are actually executing: `begin_concurrent` marks a run executing
(executor seam, T5) and `end_concurrent` frees it; at create the route calls
`has_active_run` (a check, not a reservation) so queued runs never block.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

MAX_ACTIVE_RUNS_PER_IP = 1
MAX_RUNS_PER_HOUR_PER_IP = 20
RUN_WINDOW_SECONDS = 3600
MAX_SSE_PER_IP = 3
MAX_BODY_BYTES = 32 * 1024  # 32 KiB

# Injectable clock so tests can be deterministic about the rolling hour.
_clock = time.time


def _now() -> float:
    return _clock()


_lock = threading.Lock()

# ip -> deque of recent run-creation timestamps (window RUN_WINDOW_SECONDS).
# A run is CONSUMED an hourly credit at creation time (anti-flood), regardless
# of whether it later executes.
_run_window: dict[str, deque] = defaultdict(deque)

# ip -> set of run_ids currently EXECUTING. A run occupies this slot from when
# the executor starts it until it finishes (release). At create time the route
# only CHECKS that no active run exists for the IP (returns 429 if so); the
# slot itself is taken by the executor (T5) so queued runs in the skeleton
# never hold it.
_active_runs: dict[str, set[str]] = defaultdict(set)

# ip -> set of open SSE connection tokens (never any data).
_sse_tokens: dict[str, set[int]] = defaultdict(set)
_sse_counter = 1000


def client_ip(request) -> str:
    """Effective client IP: first X-Forwarded-For hop, else socket address.

    Tolerant of test doubles that expose neither (defaults to 'testclient').
    """
    try:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    except Exception:
        pass
    try:
        client = getattr(request, "client", None)
        if client is not None:
            return client.host
    except Exception:
        pass
    return "testclient"


def check_hourly(ip: str) -> tuple[bool, str]:
    """Prune the rolling window and tell whether a new run may be created.

    Does NOT consume a credit; call accept_run_created to do that. Returns
    (ok, reason) where reason is "run_rate_limit" when the hour is full.
    """
    with _lock:
        q = _run_window[ip]
        while q and _now() - q[0] > RUN_WINDOW_SECONDS:
            q.popleft()
        if len(q) >= MAX_RUNS_PER_HOUR_PER_IP:
            return False, "run_rate_limit"
    return True, ""


def accept_run_created(ip: str) -> None:
    """Consume one hourly credit for a newly created run."""
    with _lock:
        _run_window[ip].append(_now())


def has_active_run(ip: str) -> bool:
    """True when this IP already has an executing run (concurrency cap)."""
    with _lock:
        act = _active_runs.get(ip)
        return bool(act)


def begin_concurrent(ip: str, run_id: str) -> bool:
    """Mark a run as executing (executor seam). False if the IP already has
    an executing run (MAX_ACTIVE_RUNS_PER_IP reached)."""
    with _lock:
        act = _active_runs[ip]
        if act:
            return False
        act.add(run_id)
    return True


def end_concurrent(ip: str, run_id: str) -> None:
    """Free the executing slot when a run finishes or expires."""
    with _lock:
        act = _active_runs.get(ip)
        if act:
            act.discard(run_id)
            if not act:
                _active_runs.pop(ip, None)


def sse_acquire(ip: str) -> int | None:
    """Open an SSE connection token, or None if the per-IP cap is reached."""
    global _sse_counter
    with _lock:
        toks = _sse_tokens[ip]
        if len(toks) >= MAX_SSE_PER_IP:
            return None
        _sse_counter += 1
        tok = _sse_counter
        toks.add(tok)
        return tok


def sse_release(ip: str, tok: int) -> None:
    with _lock:
        toks = _sse_tokens.get(ip)
        if toks:
            toks.discard(tok)
            if not toks:
                _sse_tokens.pop(ip, None)


def clear_all() -> None:
    """Reset every counter/queue (test isolation only; no production call)."""
    global _sse_counter
    with _lock:
        _active_runs.clear()
        _run_window.clear()
        _sse_tokens.clear()
        _sse_counter = 1000