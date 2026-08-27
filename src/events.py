"""In-process event bus  -- decouples pipeline progress from the SSE broadcaster.

The dashboard subscribes a coroutine callback; the pipeline emits typed events
(phase_started, phase_done, agent_turn, verdict, etc.) without importing the
web layer. This is what lets the UI show *live* turn/progress indicators while
keeping the pipeline testable headless (no callback = events are dropped).

All callbacks are wrapped in try/except  -- an event-sink failure must never
break the debate.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Awaitable, Callable

# A subscriber is an async callable taking (event_name: str, payload: dict).
_Subscriber = Callable[[str, dict], Awaitable[None]]

_subscribers: list[_Subscriber] = []
_lock = threading.Lock()

# Per-run sinks, keyed by run_id. The web layer registers one sink per created
# run so typed events (agent_started/agent_finished/run_failed) reach the right
# per-run SSE stream (D4: no cross-run broadcast).
_run_sinks: dict[str, _Subscriber] = {}
_sink_lock = threading.Lock()


def register_run_sink(run_id: str, cb: _Subscriber) -> None:
    """Bind a run_id to an async (event, payload) sink. Replaces any prior."""
    with _sink_lock:
        _run_sinks[run_id] = cb


def unregister_run_sink(run_id: str) -> None:
    """Detach a run_id's sink (run finished / client disconnected)."""
    with _sink_lock:
        _run_sinks.pop(run_id, None)


def subscribe(cb: _Subscriber) -> None:
    """Register an async callback. Idempotent-ish; duplicates are allowed."""
    with _lock:
        _subscribers.append(cb)


def unsubscribe(cb: _Subscriber) -> None:
    with _lock:
        try:
            _subscribers.remove(cb)
        except ValueError:
            pass


def _run_sink_for(payload: dict) -> _Subscriber | None:
    """Return the sink bound to payload's run_id, if any."""
    run_id = payload.get("run_id")
    if not run_id:
        return None
    with _sink_lock:
        return _run_sinks.get(run_id)


def _emit_sync(event: str, payload: dict) -> None:
    """Fire-and-forget emit. Schedules the coroutine on the running loop if
    there is one; otherwise runs a tiny throwaway loop. Never raises."""
    with _lock:
        subs = list(_subscribers)
    sink = _run_sink_for(payload)
    if not subs and sink is None:
        return
    callbacks = list(subs) + ([sink] if sink is not None else [])
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop  -- run callbacks synchronously on a fresh loop.
        async def _run():
            for cb in callbacks:
                try:
                    await cb(event, payload)
                except Exception:
                    pass
        try:
            asyncio.run(_run())
        except Exception:
            pass
        return

    for cb in callbacks:
        try:
            loop.create_task(cb(event, payload))
        except Exception:
            pass


def emit(event: str, payload: dict | None = None) -> None:
    """Emit an event to all subscribers (non-blocking, never raises)."""
    _emit_sync(event, payload or {})


# Convenience emitters used by the pipeline.
def phase_started(phase: str, agent: str, run_id: str | None = None) -> None:
    emit("phase_started", {"phase": phase, "agent": agent, "run_id": run_id})


def phase_done(phase: str, run_id: str | None = None) -> None:
    emit("phase_done", {"phase": phase, "run_id": run_id})


def agent_turn(agent: str, text: str, run_id: str | None = None) -> None:
    emit("agent_turn", {"agent": agent, "text": text, "run_id": run_id})
