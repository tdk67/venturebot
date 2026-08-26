"""Fork 2: review_fork  -- fire-and-forget LLM analysis of the last turn (PRD Sec. 5.3).

Analyzes the just-completed turn and proposes what to save to memory. This
module is intentionally decoupled from the LLM client so the analysis
*function* is unit-testable without a live model: `analyze_turn` takes an
injected `llm_call` callable. The default `llm_call` uses Gemini via ADK.

Throttled the same 120s cooldown as auto_capture. Fire-and-forget: the
caller is expected to schedule this with `asyncio.create_task` and an error
handler, so the user's response is never blocked.
"""
from __future__ import annotations

import json
import logging
import re

from ._throttle import try_claim
from .sqlite_store import MemoryStore

logger = logging.getLogger(__name__)

_REVIEW_PROMPT = """You are VentureBot's self-improvement engine. Analyze this turn.

SESSION EVENTS:
{transcript}

CURRENT AGENT MEMORY:
{lessons}

Analyze:
1. What did the agent do WELL? (technique to reinforce)
2. What did the agent do POORLY? (mistake to avoid repeating)
3. Would a different approach have produced a better result?
4. Should this idea stay ACTIVE, be PARKED, or PRUNED?
5. What ONE new rule/technique should be added to agent memory?

Output STRICT JSON only:
{{
  "reinforce": ["string technique name"],
  "avoid": ["string mistake description"],
  "new_technique": null or {{"name": "string", "rule": "string"}},
  "retire_technique": null or "string technique name",
  "idea_status": "ACTIVE | PARK | PRUNE",
  "idea_status_reason": "string"
}}
"""


def _extract_json(text: str) -> dict | None:
    """Best-effort JSON extraction from an LLM response. Returns None on failure."""
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _default_llm_call(prompt: str) -> str:
    """Live Gemini call via ADK (used when no mock is injected)."""
    from google.adk.agents import LlmAgent
    from google.adk.models import Gemini
    from google.adk import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    import asyncio

    from .. import config

    agent = LlmAgent(
        name="review_fork",
        model=Gemini(model=config.MODEL_RESEARCHER),
        instruction="You are a self-improvement curator. Output strict JSON only.",
        tools=[],
    )

    async def _run() -> str:
        ss = InMemorySessionService()
        sid = (await ss.create_session(app_name="venturebot", user_id="review")).id
        runner = Runner(agent=agent, session_service=ss, app_name="venturebot")
        content = types.Content(role="user", parts=[types.Part(text=prompt)])
        out = ""
        async for ev in runner.run_async(user_id="review", session_id=sid, new_message=content):
            if ev.content and ev.content.parts and not ev.partial:
                out = "".join(p.text for p in ev.content.parts if getattr(p, "text", None))
        return out

    return asyncio.run(_run())


def build_prompt(transcript: str, lessons: list[dict]) -> str:
    lesson_text = "\n".join(
        f"- {l['name']}: {l['rule']}" for l in lessons
    ) or "(none yet)"
    return _REVIEW_PROMPT.format(transcript=transcript, lessons=lesson_text)


def apply_review(store: MemoryStore, analysis: dict) -> dict:
    """Apply a review_fork analysis to the memory store. Returns a summary."""
    summary = {"techniques_added": [], "techniques_retired": [], "lessons_added": []}

    new_technique = analysis.get("new_technique")
    if isinstance(new_technique, dict) and new_technique.get("name"):
        store.save_technique(
            new_technique["name"],
            new_technique.get("rule", ""),
            new_technique.get("when_to_use", ""),
        )
        summary["techniques_added"].append(new_technique["name"])

    retire_name = analysis.get("retire_technique")
    if retire_name:
        n = store.retire_technique(retire_name)
        if n:
            summary["techniques_retired"].append(retire_name)

    for rule in analysis.get("avoid", []) or []:
        if isinstance(rule, str) and rule.strip():
            store.save_lesson("avoid", rule, "review_fork")
            summary["lessons_added"].append(rule)
    for rule in analysis.get("reinforce", []) or []:
        if isinstance(rule, str) and rule.strip():
            store.save_lesson("reinforce", rule, "review_fork")
            summary["lessons_added"].append(rule)

    return summary


def analyze_turn(store: MemoryStore, transcript: str,
                 throttle_state: dict | None = None,
                 llm_call=None) -> dict | None:
    """Run the review fork for one turn. Returns the applied summary or None.

    Returns None when throttled, when the transcript is empty, or when the
    LLM produces no parseable analysis (fail-soft: the fork is best-effort
    and must never block or crash the main pipeline).
    """
    if not transcript or not transcript.strip():
        return None
    if not try_claim(throttle_state, "review_fork"):
        return None
    llm_call = llm_call or _default_llm_call
    try:
        lessons = store.get_lessons(active_only=True, limit=20)
        raw = llm_call(build_prompt(transcript, lessons))
        analysis = _extract_json(raw)
        if analysis is None:
            logger.warning("review_fork: LLM returned unparseable JSON; skipping")
            return None
        return apply_review(store, analysis)
    except Exception:
        logger.exception("review_fork: analysis failed")
        return None
