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
import os
import re
import tempfile
import time
from dataclasses import dataclass, field

from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .. import config, run_manager, store
from ..artifact_scanner import proof_read_gate, scan_artifact
from ..events import agent_turn, phase_done, phase_started
from ..memory.auto_capture import capture_turn
from ..memory.review_fork import analyze_turn
from ..memory.sqlite_store import get_store
from ..memory.tagging import extract_tags
from ..steering import SteeringInbox
from ..url_fetch import fetch_urls
from .agents import ALL_AGENTS


# Per-session throttle state for the auto_capture fork (see _throttle).
_THROTTLE: dict[str, dict] = {}


@dataclass
class DebateResult:
    idea: str
    research_brief: str | None = None
    advocate_argument: str | None = None
    critic_rebuttal: str | None = None
    creative_angles: str | None = None
    verdict_text: str | None = None
    verdict: dict | None = None
    prd: str | None = None
    security_audit: dict | None = None
    idea_id: str | None = None
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

# ── Checkpoint persistence (crash-safe resume) ─────────────────────────

# Ordered phases for resume logic. `creative` runs after `critic` and feeds
# the Judge, so it sits between them.
_PHASES = ["research", "advocate", "critic", "creative", "judge", "prd_writer", "auditor"]


def _checkpoint_path(run_id: str) -> Path:
    return config.CHECKPOINT_DIR / f"{run_id}.json"


def _archive_path(run_id: str) -> Path:
    return config.ARCHIVE_DIR / f"{run_id}.json"


def save_checkpoint(result: DebateResult, phase: str) -> None:
    """Atomically persist DebateResult at the current phase.

    Called after each agent turn completes. On restart, a fresh
    pipeline can resume from the last saved phase.
    """
    if not result.events:
        return  # Nothing to save — don't write empty checkpoints
    run_id = run_manager.manager.run_id
    if not run_id:
        return
    snapshot = {
        "idea": result.idea,
        "idea_id": result.idea_id,
        "run_id": run_id,
        "current_phase": phase,
        "status": result.status,
        "research_brief": result.research_brief,
        "advocate_argument": result.advocate_argument,
        "critic_rebuttal": result.critic_rebuttal,
        "creative_angles": result.creative_angles,
        "verdict_text": result.verdict_text,
        "verdict": result.verdict,
        "prd": result.prd,
        "security_audit": result.security_audit,
        "error": result.error,
        "events": result.events,
        "saved_at": time.time(),
    }
    config.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(config.CHECKPOINT_DIR))
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(snapshot, f, indent=2)
        os.replace(tmp_path, str(_checkpoint_path(run_id)))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        # Best-effort; never crash the pipeline over a checkpoint write


def load_checkpoint(run_id: str) -> dict | None:
    """Load a checkpoint snapshot from disk. Returns None if not found."""
    path = _checkpoint_path(run_id)
    try:
        if path.exists():
            return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return None


def list_checkpoints() -> list[dict]:
    """Return metadata about all checkpointed runs for the dashboard."""
    snapshots: list[dict] = []
    try:
        config.CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        for p in sorted(config.CHECKPOINT_DIR.glob("*.json"),
                        key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                data = json.loads(p.read_text())
                snapshots.append({
                    "run_id": data.get("run_id", p.stem),
                    "idea": data.get("idea", "")[:200],
                    "phase": data.get("current_phase", "unknown"),
                    "status": data.get("status", "unknown"),
                    "saved_at": data.get("saved_at", 0),
                })
            except (json.JSONDecodeError, OSError):
                pass
    except OSError:
        pass
    return snapshots


def finalize_checkpoint(run_id: str) -> None:
    """Move a completed checkpoint to the archive directory."""
    src = _checkpoint_path(run_id)
    if not src.exists():
        return
    config.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(str(src), str(_archive_path(run_id)))
    except OSError:
        pass


def _result_from_snapshot(snap: dict) -> DebateResult:
    """Reconstruct a DebateResult from a checkpoint snapshot."""
    result = DebateResult(idea=snap.get("idea", ""))
    result.research_brief = snap.get("research_brief")
    result.advocate_argument = snap.get("advocate_argument")
    result.critic_rebuttal = snap.get("critic_rebuttal")
    result.creative_angles = snap.get("creative_angles")
    result.verdict_text = snap.get("verdict_text")
    result.verdict = snap.get("verdict")
    result.prd = snap.get("prd")
    result.security_audit = snap.get("security_audit")
    result.idea_id = snap.get("idea_id")
    result.status = snap.get("status", "running")
    result.error = snap.get("error")
    result.events = snap.get("events", []) or []
    return result


def _snapshot_phase_rank(phase: str) -> int:
    """Map a checkpoint `current_phase` value to its position in the pipeline.

    Phases after the judge are post-verdict: 'verdict' means the verdict was
    parsed and the gate is next; 'auditor' means the PRD was written;
    'needs_approval' means everything finished and we only await the human.
    """
    order = {
        "research": 0,
        "advocate": 1,
        "critic": 2,
        "creative": 3,
        "judge": 4,
        "verdict": 5,
        "prd_writer": 6,
        "auditor": 7,
        "needs_approval": 8,
    }
    return order.get(phase, -1)


async def resume_from_checkpoint(run_id: str, *, inbox: SteeringInbox | None = None,
                                session_id: str | None = None) -> DebateResult:
    """Resume a debate from its last saved checkpoint (C5).

    Reconstructs the DebateResult from disk and re-runs only the agents that
    have not yet completed, using the stored research_brief/argument/rebuttal
    as accumulated context. A fresh InMemorySessionService is created — the
    orchestrator feeds prior text into each agent's prompt, so the ADK session
    history is not required to resume.
    """
    snapshot = load_checkpoint(run_id)
    if not snapshot:
        raise KeyError(f"No checkpoint found for run_id {run_id}")

    inbox = inbox or SteeringInbox()
    result = _result_from_snapshot(snapshot)
    phase = snapshot.get("current_phase", "research")
    run_manager.manager.start(run_id)

    session_service = InMemorySessionService()
    sid = session_id or (
        await session_service.create_session(app_name="venturebot", user_id="user")
    ).id
    user_id = "user"

    try:
        return await _run_pipeline(result, phase, session_service, sid,
                                   user_id, inbox, run_id)
    except run_manager.RunCancelled:
        result.status = "stopped"
        store.set_status("stopped")
        _archive_result(result, run_id)
        return result
    except Exception as e:
        result.status = "failed"
        result.error = f"{type(e).__name__}: {e}"
        store.set_status("failed")
        store.log("System", "core", f"Pipeline failed: {result.error}")
        _archive_result(result, run_id)
        return result


def _checkpoint_and_log(result: DebateResult, phase: str) -> None:
    """Convenience: save checkpoint after each agent turn."""
    save_checkpoint(result, phase)


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
            agent_turn(agent_label, t, run_manager.manager.run_id)
            # Fork 1: auto_capture — persist the completed turn (best-effort).
            capture_turn(session_id, agent_label, "agent_message", t,
                         _THROTTLE.setdefault(session_id, {}))
    # Fork 2: review_fork — fire-and-forget LLM analysis of the turn.
    # Never blocks or crashes the pipeline; throttled to 120s per session.
    final = _final_text_of(events)
    if final:
        asyncio.create_task(_spawn_review_fork(session_id, final))
    return final


async def _spawn_review_fork(session_id: str, transcript: str) -> None:
    """Run the review fork off the critical path, swallowing its own errors."""
    try:
        await asyncio.to_thread(
            analyze_turn, get_store(), transcript,
            _THROTTLE.setdefault(session_id, {}),
        )
    except Exception:
        # The fork is best-effort; a failure here must never surface.
        pass


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


async def _run_pipeline(result: DebateResult, phase: str, session_service, sid,
                        user_id, inbox: SteeringInbox, run_id: str) -> DebateResult:
    """Run the debate from `phase` forward, mutating `result` in place.

    Shared by `run_debate` (starts at "research") and `resume_from_checkpoint`
    (starts at the stored `current_phase`). No error handling here — callers
    own the RunCancelled/Exception wrapping.
    """
    rank = _snapshot_phase_rank(phase)
    brief = result.research_brief
    argument = result.advocate_argument
    rebuttal = result.critic_rebuttal
    angles = result.creative_angles

    if rank <= 0:
        # 1. Researcher — with user URLs + steering checkpoint
        store.update_task("t1", "in_progress")
        store.log("System", "core", f"Researching idea: {result.idea[:120]}")
        phase_started("research", "Researcher", run_id)

        # Fetch user-provided URLs (ingested at this checkpoint)
        urls = inbox.drain_urls()
        url_digest = ""
        if urls:
            store.log("System", "core", f"Fetching {len(urls)} user-provided URL(s)...")
            url_digest = fetch_urls(urls)
            if url_digest:
                store.log("System", "core", "URL research material ingested.")

        research_msg = f"Research this idea: {result.idea}"
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
        phase_done("research", run_id)
        _checkpoint_and_log(result, "advocate")
        run_manager.manager.check()

    if rank <= 1:
        # 2. Advocate (blind: brief only) — steering checkpoint
        store.update_task("t2", "in_progress")
        store.log("System", "core", "Advocate building the case...")
        phase_started("advocate", "Advocate", run_id)
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
        phase_done("advocate", run_id)
        _checkpoint_and_log(result, "critic")
        run_manager.manager.check()

    if rank <= 2:
        # 3. Critic (has web search) — steering checkpoint
        store.update_task("t3", "in_progress")
        store.log("System", "core", "Critic challenging the Advocate...")
        phase_started("critic", "Critic", run_id)
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
        phase_done("critic", run_id)
        _checkpoint_and_log(result, "creative")
        run_manager.manager.check()

    if rank <= 3:
        # 3b. Creative Ideator — divergent head that hunts the niche the
        # precise Advocate/Critic/Judge cannot. Runs hot (higher temperature),
        # blind to search; its angles are evidence-checked by the Judge.
        store.log("System", "core", "Creative Ideator hunting the niche...")
        phase_started("creative", "Creative", run_id)
        creative_msg = _inject_steering(
            f"Research Brief:\n\n{brief}\n\nAdvocate:\n\n{argument}\n\nCritic's challenges:\n\n{rebuttal}\n\nFind the niche, pivots, unfair advantages and wild ideas.",
            _steering_block(inbox, "CHECKPOINT: creative"),
        )
        angles = await _run_agent(
            ALL_AGENTS["creative"], session_service, sid, user_id,
            creative_msg, "Creative", result,
        )
        result.creative_angles = angles
        phase_done("creative", run_id)
        _checkpoint_and_log(result, "judge")
        run_manager.manager.check()

    if rank <= 4:
        # 4. Judge (structured verdict) — steering checkpoint. The Judge sees
        # the Creative angles so it can recommend a *niche* rather than a bare
        # reject when the original framing is crowded.
        store.update_task("t4", "in_progress")
        store.log("System", "core", "Judge deliberating...")
        phase_started("judge", "Judge", run_id)
        judge_msg = _inject_steering(
            f"Research Brief:\n\n{brief}\n\nAdvocate:\n\n{argument}\n\nCritic:\n\n{rebuttal}\n\nCreative angles:\n\n{angles}\n\nProduce your structured verdict.",
            _steering_block(inbox, "CHECKPOINT: judge"),
        )
        verdict_text = await _run_agent(
            ALL_AGENTS["judge"], session_service, sid, user_id,
            judge_msg, "Judge", result,
        )
        result.verdict_text = verdict_text
        result.verdict = _parse_verdict(verdict_text)
        store.update_task("t4", "done")
        phase_done("judge", run_id)
        _checkpoint_and_log(result, "verdict")
        run_manager.manager.check()

    if rank <= 5:
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

    if rank <= 6:
        # 6. PRD Writer + Security Auditor (only on PROCEED)
        return await _write_prd(result, session_service, sid, user_id, inbox, run_id)

    # Everything up to and including the auditor already completed.
    return result


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

    # Record the idea in the idea tree (M3) for later dream-review pruning.
    try:
        result.idea_id = get_store().create_idea(idea[:200])
        get_store().update_idea_content(result.idea_id, workspace_path=f"runs/{run_id}/")
    except Exception:
        pass  # memory is best-effort; never block the debate

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
        return await _run_pipeline(result, "research", session_service, sid,
                                   user_id, inbox, run_id)

    except run_manager.RunCancelled:
        result.status = "stopped"
        store.set_status("stopped")
        _archive_result(result, run_id)
        return result
    except Exception as e:
        result.status = "failed"
        result.error = f"{type(e).__name__}: {e}"
        store.set_status("failed")
        store.log("System", "core", f"Pipeline failed: {result.error}")
        _archive_result(result, run_id)
        return result


def _archive_result(result: DebateResult, run_id: str) -> None:
    """Finalize a finished/stopped/failed run (C6 + C8).

    Populates the idea_tree row with the debate outputs (research brief,
    transcript, PRD, verdict, scores) and moves the checkpoint from the
    in-progress dir to the immutable archive. Best-effort: never raise.
    """
    try:
        s = get_store()
        idea_id = result.idea_id
        if not idea_id:
            rows = s.get_idea_tree()
            # Fallback: match the most recently created idea by title.
            idea_id = rows[0]["id"] if rows else None
        if idea_id:
            s.update_idea_content(
                idea_id,
                research_brief=result.research_brief,
                debate_transcript=json.dumps(result.events),
                prd_text=result.prd,
                verdict=(result.verdict or {}).get("verdict"),
            )
            scores = (result.verdict or {}).get("scores")
            if scores:
                s.update_idea_scores(idea_id, scores)
            if result.status == "done":
                s.update_idea_status(idea_id, "ACTIVE")
            elif result.status in ("stopped", "failed"):
                s.update_idea_status(idea_id, "PARK", f"status={result.status}")
    except Exception:
        pass  # archive write is best-effort; the checkpoint file still moves
    finalize_checkpoint(run_id)


async def _write_prd(result: DebateResult, session_service, sid, user_id,
                     inbox: SteeringInbox, run_id: str) -> DebateResult:
    """Run the PRD Writer + Security Auditor after a PROCEED verdict."""
    store.update_task("t5", "in_progress")
    store.log("System", "core", "PRD Writer drafting the PRD...")
    phase_started("prd_writer", "PRD Writer", run_id)
    prd_msg = _inject_steering(
        f"Research Brief:\n\n{result.research_brief}\n\nAdvocate:\n\n{result.advocate_argument}\n\nCritic:\n\n{result.critic_rebuttal}\n\nCreative angles:\n\n{result.creative_angles}\n\nVerdict:\n\n{result.verdict_text}\n\nWrite the PRD.",
        _steering_block(inbox, "CHECKPOINT: prd_writer"),
    )
    prd = await _run_agent(
        ALL_AGENTS["prd_writer"], session_service, sid, user_id,
        prd_msg, "PRD Writer", result,
    )
    result.prd = prd
    store.update_task("t5", "done")
    phase_done("prd_writer", run_id)
    _checkpoint_and_log(result, "auditor")

    # S10 — proof-read gate: deterministic scanner + LLM Security Auditor.
    store.log("System", "core", "Security Auditor proof-reading the PRD...")
    phase_started("auditor", "Security Auditor", run_id)
    scan = scan_artifact(prd or "", kind="text")
    audit_text = await _run_agent(
        ALL_AGENTS["auditor"], session_service, sid, user_id,
        (
            f"Proof-read this PRD (and research brief for context).\n\n"
            f"RESEARCH BRIEF:\n{result.research_brief}\n\n"
            f"PRD:\n{prd}\n\n"
            f"Return your structured verdict."
        ),
        "Security Auditor", result,
    )
    audit = _parse_audit(audit_text)
    result.security_audit = proof_read_gate(
        scanner_ok=scan.ok,
        audit_verdict=audit.get("verdict") if audit else None,
        findings=[f.to_dict() for f in scan.findings]
                  + (audit.get("findings", []) if audit else []),
    )
    store.log(
        "Security Auditor", config.MODEL_AUDITOR,
        f"Gate: {'PASS' if result.security_audit['ok'] else 'FLAG'} "
        f"({len(result.security_audit['findings'])} finding(s))",
    )
    phase_done("auditor", run_id)

    result.status = "needs_approval"
    store.log("System", "core", "PRD ready — awaiting human approval.")
    _checkpoint_and_log(result, "needs_approval")
    _save_session(run_id, result, session_service, sid, user_id)
    # Persist the PRD + audit to the idea tree before the human gates it,
    # so the archive is complete even if the server restarts at this gate.
    _archive_result(result, run_id)
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
        _archive_result(result, run_id)
        return result

    if decision == "rebut":
        # Re-enter the debate loop at Advocate with the human's fresh steering
        # (UI_UX_NOTES #3). The verdict is discarded and Advocate→Critic→
        # Creative→Judge re-run with the new evidence.
        store.log("Human", "user", "REBUT — re-running debate with new steering.")
        result.status = "running"
        result.verdict = None
        result.verdict_text = None
        run_manager.manager.start(run_id)
        return await _run_pipeline(
            result, "advocate", session_service, sid, user_id, inbox, run_id
        )

    if decision == "proceed":
        store.log("Human", "user", "PROCEED ANYWAY — forcing PRD Writer.")
        result.status = "running"
        return await _write_prd(result, session_service, sid, user_id, inbox, run_id)

    if decision == "approve":
        result.status = "done"
        store.set_status("approved")
        store.log("Human", "user", "PRD APPROVED.")
        _archive_result(result, run_id)
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


def _parse_audit(text: str) -> dict:
    """Parse the Security Auditor's structured output (PASS|FLAG + findings).

    Unlike the Judge, the Auditor's verdict is not a gating path decision —
    so a failure to parse is fail-soft (log + treat as unverified), and the
    proof-read gate surfaces it for human decision.
    """
    if not text:
        store.log("System", "core", "Warning: Security Auditor produced no output.")
        return {}
    try:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, dict):
                return parsed
    except json.JSONDecodeError as e:
        store.log("System", "core", f"Auditor JSON was not valid ({e}).")
    # Keyword fallback for a bare PASS/FLAG verdict.
    upper = text.upper()
    if "PASS" in upper and "FLAG" not in upper:
        return {"verdict": "PASS", "findings": []}
    if "FLAG" in upper:
        return {"verdict": "FLAG", "findings": []}
    store.log("System", "core", "Warning: Could not determine Auditor verdict.")
    return {}


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
