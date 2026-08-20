"""Stub debate server — fake in-memory debate engine for UI/UX tuning.

Run instead of the real ADK debate engine when you want to iterate on the UI
without spending a single LLM token:

    ./venv/bin/uvicorn src.stub_server:app --host 127.0.0.1 --port 8091

It re-uses the real dashboard routes (auth, ideas, facets, checkpoints, SSE)
so the UI is identical, but every debate is a scripted in-memory run that
auto-advances through the phases on a timer. Set VENTUREBOT_STUB=1 to force it
even when run under the normal dashboard entrypoint.

This exists specifically to fix the UI findings in UI_UX_NOTES.md:
  1. debate start feedback         2. turn/progress indicator
  3. rebut option at verdict gate  4. duplicate check   5. delete idea
  6. usage/cost visibility
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

# Import the real dashboard app *first* so all routes are registered, then
# swap the debate runner with the stub. This keeps auth/ideas/SSE identical.
from . import dashboard as _real  # noqa: F401
from .dashboard import _broadcast, _inbox, app  # noqa: F401
from .agents.pipeline import DebateResult


# ── Scripted debate ─────────────────────────────────────────────────────
_PHASES = [
    ("research", "Researcher", "🔍 Researching the idea across the web…"),
    ("advocate", "Advocate", "⚖️ Building the strongest case FOR the idea…"),
    ("critic", "Critic", "🛡️ Red-teaming every claim with counter-evidence…"),
    ("creative", "Creative", "💡 Hunting the niche, pivots and unfair advantages…"),
    ("judge", "Judge", "🧑‍⚖️ Weighing both sides into a structured verdict…"),
]

# The stub verdict intentionally exercises the PARK gate so the UI's rebut
# option can be tested end-to-end.
_STUB_VERDICT = {
    "verdict": "PARK",
    "verdict_rationale": (
        "The idea is credible but crowded. Multiple direct competitors exist, "
        "so the original framing needs a sharper niche before building."
    ),
    "scores": {
        "novelty": {"score": 5, "rationale": "Some differentiation, crowded space."},
        "feasibility": {"score": 8, "rationale": "Standard stack, ~10h build."},
        "market_fit": {"score": 6, "rationale": "Clear demand, defined but served audience."},
        "overall_average": 6.3,
    },
    "key_risks": [
        "Direct competitor X already covers the core flow (see research brief).",
        "No obvious wedge against incumbent distribution.",
    ],
    "architecture_decisions": [
        {"topic": "hosting", "decision": "FastAPI + Postgres", "advocate_position": "monolith", "critic_position": "serverless", "chosen_approach": "monolith", "rationale": "fast to demo"},
    ],
}


async def _stub_debate(idea: str) -> DebateResult:
    """Fake run: emit phase/turn events on a timer, then PARK at the gate."""
    result = DebateResult(idea=idea)
    await _broadcast("run_started", {"idea": idea})

    transcript = []
    for phase, agent, blurb in _PHASES:
        await _broadcast("phase_started", {"phase": phase, "agent": agent})
        await asyncio.sleep(1.2)  # scripted pacing
        text = f"{blurb}\n\n(Stub) The {agent} has completed its turn for: {idea}"
        result.events.append({"agent": agent, "text": text})
        transcript.append(f"[{agent}] {text}")
        await _broadcast("agent_turn", {"agent": agent, "text": text})
        await _broadcast("phase_done", {"phase": phase})

    result.research_brief = (
        f"# Research Brief — {idea}\n\n"
        "- Prior art: CompetitorX (competitorx.com), CompetitorY (competitory.io)\n"
        "- Market signal: active community, clear but served demand\n"
    )
    result.creative_angles = (
        "# Creative Angles\n\n"
        "1. **Niche**: focus on a single underserved vertical (e.g. solo consultants).\n"
        "2. **Unfair advantage**: founder's distribution into that vertical.\n"
        "3. **Wild idea**: become the 'notion for X' rather than a point tool.\n"
    )
    result.verdict = _STUB_VERDICT
    result.verdict_text = str(_STUB_VERDICT)
    result.status = "needs_verdict"

    await _broadcast("run_finished", {
        "status": result.status,
        "verdict": result.verdict,
        "creative_angles": result.creative_angles,
        "has_prd": False,
        "prd": None,
        "security_audit": None,
        "error": None,
    })
    return result


# ── Patch the debate entrypoint ─────────────────────────────────────────
# Replace the real /api/run-phase1 handler with the stub. FastAPI stores the
# route at import; we re-register a stub route on the same path (later
# registration wins for matching). Simpler + robust: monkeypatch the underlying
# task function that the real route calls.
from . import dashboard as _dash  # noqa: E402


async def _stub_loop(idea: str):
    await _stub_debate(idea)


_dash._run_phase1_loop = _stub_loop  # type: ignore[attr-defined]


# ── App entrypoint (for `uvicorn src.stub_server:app`) ─────────────────
# `app` is the real dashboard app (already imported above). We only need to
# ensure the stub is active; the patch above does that at import time.
__all__ = ["app"]
