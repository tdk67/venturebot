"""Phase 1 pipeline orchestrator.

Runs the debate: Researcher → Advocate → Critic → Judge → (gate) → PRD Writer.

Why a custom orchestrator instead of SequentialAgent:
  1. SequentialAgent is DEPRECATED (in favor of Workflow) and runs all
     sub-agents unconditionally — no conditional verdict gate.
  2. The kill switch (S2) must be polled BETWEEN agents, not just at the start.
  3. The verdict gate (Task 7) is a real control-flow branch: PARK/PRUNE must
     stop and ask the human; PROCEED continues to PRD Writer.

The orchestrator uses ADK's Runner per agent turn, so each agent still gets a
proper session/state; we thread prior outputs forward as the next agent's
input message.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .. import config, run_manager, store
from .agents import ALL_AGENTS


@dataclass
class DebateResult:
    idea: str
    research_brief: str | None = None
    advocate_argument: str | None = None
    critic_rebuttal: str | None = None
    verdict: dict | None = None
    prd: str | None = None
    status: str = "running"  # running | needs_clarification | needs_verdict | needs_approval | done | stopped | failed
    error: str | None = None
    events: list[dict] = field(default_factory=list)


def _text_from_event(ev) -> str | None:
    if ev.content and ev.content.parts:
        texts = [p.text for p in ev.content.parts if getattr(p, "text", None)]
        if texts:
            return "\n".join(texts)
    return None


def _final_text_of(events) -> str:
    """Collect the last non-partial text from a run's events."""
    out = ""
    for ev in events:
        t = _text_from_event(ev)
        if t and not ev.partial:
            out = t
    return out


async def _run_agent(agent, session_service, session_id, user_id, message: str, agent_label: str, result: DebateResult) -> str:
    """Run one agent turn, forwarding its output. Polls kill switch."""
    run_manager.manager.check()  # kill switch between agents
    runner = Runner(agent=agent, session_service=session_service, app_name="venturebot")
    content = types.Content(role="user", parts=[types.Part(text=message)])
    events = []
    async for ev in runner.run_async(user_id=user_id, session_id=session_id, new_message=content):
        run_manager.manager.check()  # kill switch during streaming
        events.append(ev)
        t = _text_from_event(ev)
        if t and not ev.partial:
            result.events.append({"agent": agent_label, "text": t})
            store.log(agent_label, getattr(agent.model, "model", "?"), t[:200])
    return _final_text_of(events)


async def run_debate(idea: str, *, session_id: str | None = None) -> DebateResult:
    """Run the full Phase 1 debate. Cancellable via run_manager.manager.stop()."""
    from ..input_guard import guard_input

    result = DebateResult(idea=idea)
    run_manager.manager.start(store.start_run()["run_id"])

    # S5 input guard: quarantine or block injection
    guarded = guard_input(idea)
    if guarded["blocked"]:
        result.status = "failed"
        result.error = f"Input blocked by injection guard: {guarded['matches'][:3]}"
        store.set_status("failed")
        return result

    session_service = InMemorySessionService()
    sid = session_id or (await session_service.create_session(app_name="venturebot", user_id="user")).id
    user_id = "user"

    try:
        # 1. Researcher
        store.update_task("t1", "in_progress")
        store.log("System", "core", f"Researching idea: {idea[:120]}")
        brief = await _run_agent(
            ALL_AGENTS["researcher"], session_service, sid, user_id,
            f"Research this idea: {guarded['text']}", "Researcher", result,
        )
        result.research_brief = brief
        store.update_task("t1", "done")
        run_manager.manager.check()

        # 2. Advocate (blind: brief only)
        store.update_task("t2", "in_progress")
        store.log("System", "core", "Advocate building the case...")
        argument = await _run_agent(
            ALL_AGENTS["advocate"], session_service, sid, user_id,
            f"Research Brief:\n\n{brief}\n\nArgue FOR this idea.", "Advocate", result,
        )
        result.advocate_argument = argument
        store.update_task("t2", "done")
        run_manager.manager.check()

        # 3. Critic (has web search)
        store.update_task("t3", "in_progress")
        store.log("System", "core", "Critic challenging the Advocate...")
        rebuttal = await _run_agent(
            ALL_AGENTS["critic"], session_service, sid, user_id,
            f"Research Brief:\n\n{brief}\n\nAdvocate's Argument:\n\n{argument}\n\nChallenge every claim.",
            "Critic", result,
        )
        result.critic_rebuttal = rebuttal
        store.update_task("t3", "done")
        run_manager.manager.check()

        # 4. Judge (structured verdict)
        store.update_task("t4", "in_progress")
        store.log("System", "core", "Judge deliberating...")
        verdict_text = await _run_agent(
            ALL_AGENTS["judge"], session_service, sid, user_id,
            f"Research Brief:\n\n{brief}\n\nAdvocate:\n\n{argument}\n\nCritic:\n\n{rebuttal}\n\nProduce your structured verdict.",
            "Judge", result,
        )
        result.verdict = _parse_verdict(verdict_text)
        store.update_task("t4", "done")
        run_manager.manager.check()

        # 5. Verdict gate
        v = result.verdict or {}
        avg = _overall_average(v)
        if v.get("verdict") == "PRUNE" or (avg is not None and avg < 4):
            result.status = "needs_verdict"
            store.log("Judge", config.MODEL_JUDGE, f"Verdict: PRUNE (avg {avg}) — awaiting human decision")
            return result
        if v.get("verdict") == "PARK" or (avg is not None and avg < 7):
            result.status = "needs_verdict"
            store.log("Judge", config.MODEL_JUDGE, f"Verdict: PARK (avg {avg}) — awaiting human decision")
            return result

        # 6. PRD Writer (only on PROCEED)
        store.update_task("t5", "in_progress")
        store.log("System", "core", "PRD Writer drafting the PRD...")
        prd = await _run_agent(
            ALL_AGENTS["prd_writer"], session_service, sid, user_id,
            f"Research Brief:\n\n{brief}\n\nAdvocate:\n\n{argument}\n\nCritic:\n\n{rebuttal}\n\nVerdict:\n\n{verdict_text}\n\nWrite the PRD.",
            "PRD Writer", result,
        )
        result.prd = prd
        store.update_task("t5", "done")
        result.status = "needs_approval"  # PRD approval gate (human)
        store.log("System", "core", "PRD ready — awaiting human approval.")
        return result

    except run_manager.RunCancelled:
        result.status = "stopped"
        store.set_status("stopped")
        return result
    except Exception as e:
        result.status = "failed"
        result.error = f"{type(e).__name__}: {e}"
        store.set_status("failed")
        store.log("System", "core", f"Pipeline failed: {result.error}")
        return result


def _parse_verdict(text: str) -> dict:
    """Best-effort parse of the Judge's structured output (may be JSON or prose)."""
    import json
    import re
    if not text:
        return {}
    # Try to find a JSON object
    try:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
    except json.JSONDecodeError:
        pass
    # Fallback: extract verdict keyword
    upper = text.upper()
    for kw in ("PROCEED", "PARK", "PRUNE"):
        if kw in upper:
            return {"verdict": kw}
    return {"verdict": "PARK"}


def _overall_average(verdict: dict) -> float | None:
    scores = verdict.get("scores", {})
    nums = []
    for key in ("novelty", "feasibility", "market_fit"):
        s = scores.get(key, {})
        if isinstance(s, dict) and isinstance(s.get("score"), (int, float)):
            nums.append(s["score"])
    if not nums:
        return None
    return sum(nums) / len(nums)
