"""Phase 1 pipeline orchestrator — resumable, steering-aware.

Runs: Researcher → Advocate → Critic → Judge → (verdict gate) → PRD Writer.

Design principles (user requirements):
  1. User has total control: Stop kills the loop; verdict + PRD are human-gated.
  2. Steering messages and URLs are ingested at CHECKPOINTS (between agents),
     never mid-turn — so they don't corrupt an in-flight generation.
  3. User-provided research URLs are fetched and fed to the Researcher, so
     pre-existing research isn't wasted and web search has a stronger anchor.
  4. The pipeline is RESUMMABLE: when it pauses at a gate, its state is stored
     and can be resumed with a human decision + fresh steering.

Why a custom orchestrator (not SequentialAgent): SequentialAgent is deprecated,
runs sub-agents unconditionally (no conditional gate), and can't poll a kill
switch between agents.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field

from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .. import config, run_manager, store
from ..steering import SteeringInbox
from ..url_fetch import fetch_urls
from .agents import ALL_AGENTS


@dataclass
class DebateResult:
    idea: str
    research_brief: str | None = None
    advocate_argument: str | None = None
    critic_rebuttal: str | None = None
    verdict_text: str | None = None
    verdict: dict | None = None
    prd: str | None = None
    status: str = "running"  # running | needs_clarification | needs_verdict | needs_approval | done | stopped | failed
    error: str | None = None
    events: list[dict] = field(default_factory=list)


# Persisted resumable state, keyed by run_id. Lets the dashboard resume a
# paused debate (verdict/PRD gate) without losing the prior agents' work.
_SESSIONS: dict[str, dict] = {}
_SESSIONS_FILE = config.DATA_DIR / "paused_sessions.json"

def _load_sessions() -> None:
    """Load persisted sessions from disk on startup."""
    global _SESSIONS
    if _SESSIONS_FILE.exists():
        try:
            data = json.loads(_SESSIONS_FILE.read_text())
            # We can only restore metadata, not the full session objects
            # (session_service, sid, user_id are runtime-only)
            # For now, just log that sessions were lost
            if data:
                store.log("System", "core", f"Warning: {len(data)} paused sessions lost on restart (in-memory sessions cannot persist)")
        except (json.JSONDecodeError, OSError):
            pass

def _save_sessions_metadata() -> None:
    """Save session metadata to disk for observability (not resumable)."""
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        metadata = {
            run_id: {
                "status": session["result"].status,
                "idea": session["result"].idea[:200],
                "verdict": session["result"].verdict,
                "timestamp": time.time(),
            }
            for run_id, session in _SESSIONS.items()
        }
        _SESSIONS_FILE.write_text(json.dumps(metadata, indent=2))
    except OSError:
        pass  # Non-critical: metadata save failure shouldn't break the pipeline

# Load sessions on module import
_load_sessions()


def _text_from_event(ev) -> str | None:
    if ev.content and ev.content.parts:
        texts = [p.text for p in ev.content.parts if getattr(p, "text", None)]
        if texts:
            return "\n".join(texts)
    return None


def _final_text_of(events) -> str:
    out = ""
    for ev in events:
        t = _text_from_event(ev)
        if t and not ev.partial:
            out = t
    return out


async def _run_agent(agent, session_service, session_id, user_id, message: str,
                     agent_label: str, result: DebateResult) -> str:
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


def _steering_block(inbox: SteeringInbox, label: str) -> str:
    """Drain the inbox at a checkpoint and format it for the next agent."""
    parts = []
    steering = inbox.drain_steering()
    if steering:
        parts.append("USER STEERING (latest guidance — honor this):\n" +
                     "\n".join(f"- {s}" for s in steering))
    urls = inbox.drain_urls()
    if urls:
        parts.append("USER-PROVIDED RESEARCH URLS:\n" + "\n".join(f"- {u}" for u in urls))
    if parts:
        return f"\n\n===== {label} =====\n" + "\n\n".join(parts) + "\n=================\n"
    return ""


def _inject_steering(message: str, block: str) -> str:
    return message + block if block else message


async def run_debate(idea: str, *, inbox: SteeringInbox | None = None,
                     session_id: str | None = None) -> DebateResult:
    """Run the debate to its next gate. Cancellable + resumable.

    If verdict is PROCEED, runs through PRD Writer and pauses at needs_approval.
    If PARK/PRUNE, pauses at needs_verdict with full state stored in _SESSIONS.
    """
    from ..input_guard import guard_input

    inbox = inbox or SteeringInbox()
    result = DebateResult(idea=idea)
    run_id = store.start_run()["run_id"]
    run_manager.manager.start(run_id)

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
        # 1. Researcher — with user URLs + steering checkpoint
        store.update_task("t1", "in_progress")
        store.log("System", "core", f"Researching idea: {idea[:120]}")

        # Fetch user-provided URLs (ingested at this checkpoint)
        urls = inbox.drain_urls()
        url_digest = ""
        if urls:
            store.log("System", "core", f"Fetching {len(urls)} user-provided URL(s)...")
            url_digest = fetch_urls(urls)
            if url_digest:
                store.log("System", "core", "URL research material ingested.")

        research_msg = f"Research this idea: {guarded['text']}"
        if url_digest:
            research_msg += (
                "\n\nThe user has ALREADY done research and provided these URLs. "
                "Read and incorporate this material — it is authoritative context:\n\n"
                f"{url_digest}"
            )
        research_msg = _inject_steering(research_msg, _steering_block(inbox, "CHECKPOINT: research"))

        brief = await _run_agent(
            ALL_AGENTS["researcher"], session_service, sid, user_id,
            research_msg, "Researcher", result,
        )
        result.research_brief = brief
        store.update_task("t1", "done")
        run_manager.manager.check()

        # 2. Advocate (blind: brief only) — steering checkpoint
        store.update_task("t2", "in_progress")
        store.log("System", "core", "Advocate building the case...")
        advocate_msg = _inject_steering(
            f"Research Brief:\n\n{brief}\n\nArgue FOR this idea.",
            _steering_block(inbox, "CHECKPOINT: advocate"),
        )
        argument = await _run_agent(
            ALL_AGENTS["advocate"], session_service, sid, user_id,
            advocate_msg, "Advocate", result,
        )
        result.advocate_argument = argument
        store.update_task("t2", "done")
        run_manager.manager.check()

        # 3. Critic (has web search) — steering checkpoint
        store.update_task("t3", "in_progress")
        store.log("System", "core", "Critic challenging the Advocate...")
        critic_msg = _inject_steering(
            f"Research Brief:\n\n{brief}\n\nAdvocate's Argument:\n\n{argument}\n\nChallenge every claim.",
            _steering_block(inbox, "CHECKPOINT: critic"),
        )
        rebuttal = await _run_agent(
            ALL_AGENTS["critic"], session_service, sid, user_id,
            critic_msg, "Critic", result,
        )
        result.critic_rebuttal = rebuttal
        store.update_task("t3", "done")
        run_manager.manager.check()

        # 4. Judge (structured verdict) — steering checkpoint
        store.update_task("t4", "in_progress")
        store.log("System", "core", "Judge deliberating...")
        judge_msg = _inject_steering(
            f"Research Brief:\n\n{brief}\n\nAdvocate:\n\n{argument}\n\nCritic:\n\n{rebuttal}\n\nProduce your structured verdict.",
            _steering_block(inbox, "CHECKPOINT: judge"),
        )
        verdict_text = await _run_agent(
            ALL_AGENTS["judge"], session_service, sid, user_id,
            judge_msg, "Judge", result,
        )
        result.verdict_text = verdict_text
        result.verdict = _parse_verdict(verdict_text)
        store.update_task("t4", "done")
        run_manager.manager.check()

        # 5. Verdict gate
        v = result.verdict or {}
        avg = _overall_average(v)
        if v.get("verdict") == "PRUNE" or (avg is not None and avg < 4):
            result.status = "needs_verdict"
            store.log("Judge", config.MODEL_JUDGE, f"Verdict: PRUNE (avg {avg}) — awaiting human decision")
            _save_session(run_id, result, session_service, sid, user_id)
            return result
        if v.get("verdict") == "PARK" or (avg is not None and avg < 7):
            result.status = "needs_verdict"
            store.log("Judge", config.MODEL_JUDGE, f"Verdict: PARK (avg {avg}) — awaiting human decision")
            _save_session(run_id, result, session_service, sid, user_id)
            return result

        # 6. PRD Writer (only on PROCEED)
        return await _write_prd(result, session_service, sid, user_id, inbox, run_id)

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


async def _write_prd(result: DebateResult, session_service, sid, user_id,
                     inbox: SteeringInbox, run_id: str) -> DebateResult:
    """Run the PRD Writer after a PROCEED verdict."""
    store.update_task("t5", "in_progress")
    store.log("System", "core", "PRD Writer drafting the PRD...")
    prd_msg = _inject_steering(
        f"Research Brief:\n\n{result.research_brief}\n\nAdvocate:\n\n{result.advocate_argument}\n\nCritic:\n\n{result.critic_rebuttal}\n\nVerdict:\n\n{result.verdict_text}\n\nWrite the PRD.",
        _steering_block(inbox, "CHECKPOINT: prd_writer"),
    )
    prd = await _run_agent(
        ALL_AGENTS["prd_writer"], session_service, sid, user_id,
        prd_msg, "PRD Writer", result,
    )
    result.prd = prd
    store.update_task("t5", "done")
    result.status = "needs_approval"
    store.log("System", "core", "PRD ready — awaiting human approval.")
    _save_session(run_id, result, session_service, sid, user_id)
    return result


async def resume_debate(run_id: str, decision: str, steering: str | None = None,
                        urls: list[str] | None = None) -> DebateResult:
    """Resume a paused debate with a human decision (+ optional steering/URLs).

    decision: 'proceed' (force PRD Writer), 'abort' (stop), 'approve' (PRD ok),
              'reject' (PRD rejected).
    """
    session = _SESSIONS.pop(run_id, None)
    if not session:
        raise KeyError(f"No paused debate with run_id {run_id}")

    result: DebateResult = session["result"]
    session_service = session["session_service"]
    sid = session["sid"]
    user_id = session["user_id"]

    inbox = SteeringInbox()
    if steering:
        inbox.add_steering(steering)
    if urls:
        inbox.add_urls(urls)

    if decision in ("abort", "reject"):
        result.status = "stopped"
        store.set_status("stopped")
        store.log("Human", "user", f"Decision: {decision.upper()} — stopping.")
        return result

    if decision == "proceed":
        store.log("Human", "user", "PROCEED ANYWAY — forcing PRD Writer.")
        result.status = "running"
        return await _write_prd(result, session_service, sid, user_id, inbox, run_id)

    if decision == "approve":
        result.status = "done"
        store.set_status("approved")
        store.log("Human", "user", "PRD APPROVED.")
        return result

    raise ValueError(f"Unknown decision: {decision}")


def _save_session(run_id: str, result: DebateResult, session_service, sid, user_id):
    _SESSIONS[run_id] = {
        "result": result,
        "session_service": session_service,
        "sid": sid,
        "user_id": user_id,
    }
    _save_sessions_metadata()  # Persist metadata for observability


def paused_run_ids() -> list[str]:
    return list(_SESSIONS.keys())


def _parse_verdict(text: str) -> dict:
    """Parse the Judge's raw output into a verdict dict.

    The Judge agent declares output_schema=JudgeVerdict, but the pipeline
    extracts raw text (see _final_text_of), so the verdict must still be
    parsed here. Fail loud: if no verdict can be determined, raise ValueError
    — never silently default to PARK.
    """
    if not text:
        raise ValueError("Judge produced no output — cannot determine a verdict")
    # 1. Structured JSON (the normal output_schema path).
    try:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, dict) and parsed.get("verdict") in ("PROCEED", "PARK", "PRUNE"):
                return parsed
    except json.JSONDecodeError as e:
        store.log("System", "core", f"Judge JSON was not valid ({e}); falling back to keyword search.")
    # 2. Keyword search over prose output.
    upper = text.upper()
    for kw in ("PROCEED", "PARK", "PRUNE"):
        if kw in upper:
            return {"verdict": kw}
    # 3. Fail loud — an uninterpretable verdict must surface, not silently PARK.
    raise ValueError(
        f"Could not determine verdict from Judge output. Raw output: {text[:200]!r}"
    )


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
