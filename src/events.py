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


def _emit_sync(event: str, payload: dict) -> None:
    """Fire-and-forget emit. Schedules the coroutine on the running loop if
    there is one; otherwise runs a tiny throwaway loop. Never raises."""
    with _lock:
        subs = list(_subscribers)
    if not subs:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop  -- run callbacks synchronously on a fresh loop.
        async def _run():
            for cb in subs:
                try:
                    await cb(event, payload)
                except Exception:
                    pass
        try:
            asyncio.run(_run())
        except Exception:
            pass
        return

    for cb in subs:
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
