"""Stub debate server  -- fake in-memory debate engine for the live debate view (T7).

Run instead of the real ADK debate engine when you want to iterate on the live
debate UI without spending a single LLM token:

    VENTUREBOT_STUB=1 ./venv/bin/uvicorn src.stub_server:app --host 127.0.0.1 --port 8091

It re-uses the REAL dashboard routes (create/status/SSE/result/ack) so the API
contract is identical, but every debate is a SCRIPTED per-agent run that the
stub drives directly into the run record. The event stream therefore emits the
same per-agent lifecycle events the real orchestrator emits (agent_started /
agent_finished / run_finished / run_failed), which is exactly what the live
debate view consumes to render per-agent progress chips.

T7 verification (REWRITE_PLAN.md Part C, T7):
  * scripted SUCCESS -> the SSE stream shows all 7 agent steps
  * scripted FAILURE -> a run_failed event with an explicit reason arrives
    within ~2s, so the UI never sits in a stuck "thinking" state.

Mode is chosen at import time via env:
  VENTURE_STUB_MODE=success  (default) -> 7 agents finish, run_finished, result stored
  VENTURE_STUB_MODE=fail     -> first agent starts, then run_failed within ~0.4s

A separate stub process per mode is used by the E2E spec
(tests/e2e/debate.spec.md).
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request

# Import the real dashboard FIRST so all real routes (incl. the T7 SSE stream,
# result, ack) are registered. Everything stays identical except the
# create-debate route, which additionally launches a themed scripted run.
from . import dashboard as _real  # noqa: F401
from .dashboard import (
    STORE,
    _RUNS,
    _emit,
    api_create_debate as _real_create_debate,
    app,
)

# Names shown in the live view, in orchestrator order (7 agents).
_AGENTS = [
    ("researcher", "Researcher"),
    ("advocate", "Advocate"),
    ("critic", "Critic"),
    ("creative", "Creative"),
    ("judge", "Judge"),
    ("prd_writer", "PRD Writer"),
    ("auditor", "Security Auditor"),
]

# Recognised providers, so per-agent chips can show a model label that makes
# sense in the "cost/elapsed" line.
_MODELS = {
    "researcher": "model-researcher",
    "advocate": "model-advocate",
    "critic": "model-critic",
    "creative": "model-creative",
    "judge": "model-judge",
    "prd_writer": "model-prd",
    "auditor": "model-audit",
}

_STUB_MODE = os.getenv("VENTURE_STUB_MODE", "success")


async def _scripted_drive(run_id: str) -> None:
    """Drive one scripted per-agent run for a just-created run.

    Writes straight into the run's event list (`_emit`) so the existing SSE
    route (`api_debate_events`) replays exactly the per-agent events the real
    debate emits (agent_started/agent_finished/run_finished/run_failed).
    """
    run = _RUNS.get(run_id)
    if run is None:
        return
    run.status = "running"
    _emit(run, "run_started", {"run_id": run_id, "status": "running"})

    if _STUB_MODE == "fail":
        # Loud failure within ~0.4s: ONE agent starts, then a hard failure is
        # broadcast so the live view must show the red error banner (never a
        # stuck "thinking" state).
        phase, label = _AGENTS[0]
        _emit(run, "agent_started", {"agent": label, "model": _MODELS[phase], "run_id": run_id})
        await asyncio.sleep(0.4)
        reason = "Forced stub failure: simulated invalid API key (VENTURE_STUB_MODE=fail)"
        run.status = "failed"
        run.error = reason
        _emit(run, "run_failed", {"reason": reason, "run_id": run_id})
        return

    # SUCCESS: all 7 agents start and finish in order.
    transcript: list[dict[str, Any]] = []
    for phase, label in _AGENTS:
        t0 = time.time()
        _emit(run, "agent_started", {"agent": label, "model": _MODELS[phase], "run_id": run_id})
        await asyncio.sleep(0.6)  # scripted pacing so the UI visibly advances
        dur = round(time.time() - t0, 3)
        _emit(run, "agent_finished", {"agent": label, "duration": dur, "run_id": run_id})
        text = f"(stub) {label} completed its turn."
        transcript.append({"agent": label, "text": text})

    run.status = "done"
    run.result = {
        "run_id": run_id,
        "status": "done",
        "verdict": {"verdict": "PROCEED", "verdict_rationale": "stub"},
        "prd": f"# PRD (stub)\n\nFor {run.idea}\n",
        "transcript": transcript,
    }
    _emit(run, "run_finished", {"run_id": run_id, "status": "done"})


# --- Override POST /api/debates: same creation logic + launch the stub ----
def _strip_real_create_route():
    """Remove the real POST /api/debates route, keeping every other route."""
    kept = []
    for route in list(app.router.routes):
        methods = getattr(route, "methods", None) or set()
        if getattr(route, "path", None) == "/api/debates" and "POST" in methods:
            continue
        kept.append(route)
    app.router.routes = kept


_strip_real_create_route()


@app.post("/api/debates", status_code=201)
async def api_create_debate_stub(request: Request):
    """Run the real creation logic, then launch a themed per-agent stub run."""
    body = await _real_create_debate(request)  # dict {run_id, status}
    run_id = body["run_id"]
    asyncio.create_task(_scripted_drive(run_id))
    return body


__all__ = ["app"]