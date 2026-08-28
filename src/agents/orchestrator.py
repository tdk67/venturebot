"""Orchestrator  -- autonomous agentic loop that drives the debate sub-agents.

The Orchestrator is a single ADK LlmAgent with function tools that wrap each
sub-agent (Researcher, Advocate, Critic, Creative, Judge, PRD Writer, Auditor).
It runs in a loop with an iteration budget and quality-gate stopping rules.

Design:
  - The orchestrator decides WHAT to do next  -- the system prompt encodes the
    engineering process, not hardcoded phase counters.
  - Sub-agents retain their distinct models, temperatures, and tool access
    (Advocate blind, Critic has search, Creative hot).
  - clarify() is real HITL: ADK pauses, human answers, orchestrator resumes.
  - load_memories() reads past lessons so the orchestrator applies them.
  - Self-review: the orchestrator reads its own PRD, finds gaps, re-drafts.
  - Quality gate: if PRD + verdict are ready and no progress for N turns, stop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from google.adk import Runner
from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.sessions import InMemorySessionService
from google.genai import types

from . import prompts, schemas
from .clarify import clarify_question
from .. import config, store
from ..events import emit

# Import the factory function for BYOK support
from .agents import ALL_AGENTS, create_agents
from .. import run_manager
from ..events import agent_turn
from ..memory.sqlite_store import get_store
from ..memory.auto_capture import capture_turn
from ..memory.review_fork import analyze_turn
from ..steering import SteeringInbox

logger = logging.getLogger(__name__)

# -- Persisted run result (exposed to dashboard) ------------------------
_RUNS: dict[str, "OrchestratorResult"] = {}

# Throttle state for auto_capture fork
_THROTTLE: dict[str, dict] = {}

# -- Orchestrator system prompt ------------------------------------------

_ORCHESTRATOR_PROMPT = """You are IdeaLint's Orchestrator. Your job is to evaluate a startup idea
through a rigorous multi-agent engineering process. You don't debate yourself  -- 
you delegate to specialized sub-agents, each with a different perspective
and (where appropriate) different information access.

## YOUR PROCESS

Follow this engineering process. You may loop back at any point if you discover
gaps or the human provides new information.

### 1. LOAD PAST LESSONS
Before anything else, call load_memories(). This returns lessons IdeaLint
learned from previous runs. APPLY ALL OF THESE. If a lesson says "always run
security audit before presenting", you MUST run the audit. If a lesson says
"verify market size claims with search", you MUST do that.

### 2. RESEARCH
Call research(idea) to get a structured brief with prior art, market signals,
technical landscape, and resource links. If the user provided URLs, pass them.

### 3. CLARIFY
If the idea is vague, or the research reveals contradictory information, or you
need domain expertise the user might have, call clarify(question). Wait for the
answer  -- this tool PAUSES until the human responds. Then re-research with the
new information. You may clarify multiple times if needed.

### 4. DEBATE
Call advocate(), then critic(), then creative(). The Advocate is BLIND (no web
search)  -- it argues from the brief alone. The Critic HAS web search  -- it finds
counter-evidence. The Creative finds niches, pivots, and unfair advantages.

### 5. JUDGE
Call judge(). It returns a structured verdict with scores:
- Novelty (1-10), Feasibility (1-10), Market Fit (1-10)
- Overall: PROCEED (avg >=7), PARK (4-6), PRUNE (<4)

### 6. VERDICT GATE
- PROCEED: continue to PRD drafting.
- PARK or PRUNE: present the verdict to the human via clarify()  -- ask whether
  to proceed anyway, park, or abandon.

### 7. DRAFT PRD
Call write_prd(). This writes a structured PRD to the workspace.

### 8. SELF-REVIEW  -- CRITICAL QUALITY GATE
After drafting the PRD, FIRST call scan_prd() to run the deterministic completeness check. It checks for required sections, acceptance criteria, security coverage, and sourced claims. If it returns FLAG, fix the issues before continuing.

Then call read_file("PRD.md") and review it yourself:
- Are ALL required sections present? (Product Overview, Functional Requirements,
  Non-Functional Requirements, Technical Architecture, Acceptance Criteria,
  Milestones & MVP Scope, Open Questions & Risks)
- Is every factual claim backed by a cited source?
- Are security, auth, data-handling, error handling, and rate limiting covered?
- Does every functional requirement have a corresponding acceptance criterion?
- Are there any contradictions or internal inconsistencies?
If you find gaps, call write_prd() again with specific instructions to fix them.
Do NOT present an incomplete or flawed PRD to the human.

### 9. AUDIT
Call audit(). It proof-reads the PRD and returns PASS or FLAG with findings.
If FLAG, fix the flagged issues (call write_prd() with instructions), then
re-audit. Do NOT skip this step.

### 10. PRESENT TO HUMAN
Call clarify() with a summary of the PRD, the scores, and ask:
"[Approve] [Changes] [Reject]"
- If Changes: go back to RESEARCH with the human's feedback.
- If Reject: stop  -- the run is done.
- If Approve: stop  -- the run is a success.

## STOPPING RULES

You MUST stop and present whatever you have when ANY of these is true:
1. The human has approved or rejected  -> done.
2. You've used 10 turns AND have a PRD + verdict  -> present, don't loop forever.
3. You've made NO progress for 3 consecutive turns (same file contents, no
   new information gathered, no new quality issues found)  -> present what you
   have and explain what's missing.
4. If you're genuinely stuck  -- clarify() is always available. Never loop in
   confusion.

## WORKSPACE DISCIPLINE

- Write files to the workspace (RESEARCH_BRIEF.md, PRD.md).
- ALWAYS call read_file() to re-read a file before editing it.
- NEVER edit from memory  -- stale edits corrupt the artifact.
- After the human approves, save the PRD with save_artifact().

## PAST LESSONS (from load_memories())

These are lessons from previous runs. You MUST apply them  -- they exist because
the human or the system flagged a mistake that must not be repeated.

{must_read_before_starting}

---

Now begin: evaluate the user's idea following this process. Start by calling
load_memories(), then research().
"""


def _render_must_read() -> str:
    """Render the 'must read before starting' block: active lessons from the
    memory store. Rendered server-side  -- the raw {placeholder} must never reach
    ADK's instruction templating (it would raise 'Context variable not found').
    """
    try:
        lessons = get_store().get_lessons(active_only=True, limit=10)
    except Exception:
        lessons = []
    if not lessons:
        return "(No lessons recorded yet  -- this may be one of the first runs.)"
    lines = [f"- {l['name']}: {l['rule']}" for l in lessons]
    return "\n".join(lines)


def _orchestrator_instruction() -> str:
    """The orchestrator system prompt with all placeholders rendered.
    Uses replace(), not format(), so literal braces elsewhere stay intact."""
    return _ORCHESTRATOR_PROMPT.replace(
        "{must_read_before_starting}", _render_must_read()
    )


@dataclass
class OrchestratorResult:
    """The final result of an orchestrator run, exposed to the dashboard."""
    idea: str
    idea_id: str | None = None
    status: str = "running"  # running | needs_clarification | needs_verdict | needs_approval | done | stopped | failed
    research_brief: str | None = None
    advocate_argument: str | None = None
    critic_rebuttal: str | None = None
    creative_angles: str | None = None
    verdict_text: str | None = None
    verdict: dict | None = None
    prd: str | None = None
    security_audit: dict | None = None
    error: str | None = None
    events: list[dict] = field(default_factory=list)
    turns_used: int = 0
    clarification_question: str | None = None
    clarification_state: str | None = None  # "awaiting_response" when clarify is active
    # The human's answer to a paused clarification  -- set when resuming from
    # disk; consumed by the first turn prompt after resume.
    clarification_answer: str | None = None
    # Per-run snapshot bookkeeping (idea_runs row)  -- set by run_orchestrator.
    idea_run_id: str | None = None
    resume_comment: str | None = None


class ClarifyPaused(Exception):
    """Raised by the clarify() tool to tear the run down durably.

    The debate PAUSES (all state persisted to disk) until the human answers.
    There is no timeout on purpose: the user may answer in 2 minutes or next
    week, possibly after the server restarted. Resumption rebuilds everything
    from the persisted pause record.
    """

    def __init__(self, question: str):
        super().__init__(question)
        self.question = question


# -- Durable pause store ------------------------------------------------

def _pause_dir() -> Path:
    d = config.DATA_DIR / "paused_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pause_path(run_id: str) -> Path:
    return _pause_dir() / f"{run_id}.json"


def persist_pause(result: "OrchestratorResult", run_id: str) -> dict:
    """Snapshot the full debate state so it survives server restarts."""
    payload = {
        "run_id": run_id,
        "asked_at": time.time(),
        "question": result.clarification_question,
        "idea": result.idea,
        "idea_id": result.idea_id,
        "idea_run_id": result.idea_run_id,
        "resume_comment": result.resume_comment,
        "turns_used": result.turns_used,
        "research_brief": result.research_brief,
        "advocate_argument": result.advocate_argument,
        "critic_rebuttal": result.critic_rebuttal,
        "creative_angles": result.creative_angles,
        "verdict_text": result.verdict_text,
        "verdict": result.verdict,
        "prd": result.prd,
        "security_audit": result.security_audit,
        "events": result.events[-100:],  # cap transcript tail for the pause file
    }
    write_pause(payload)
    return payload


def write_pause(payload: dict) -> None:
    """Write (or re-write) a pause snapshot dict atomically."""
    tmp = _pause_path(payload["run_id"]).with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_pause_path(payload["run_id"]))


def get_pause(run_id: str) -> dict | None:
    p = _pause_path(run_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def any_pending_pause() -> dict | None:
    """The oldest pending clarification across restarts (for /api/state)."""
    pauses = []
    for p in _pause_dir().glob("*.json"):
        try:
            pauses.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    if not pauses:
        return None
    return min(pauses, key=lambda x: x.get("asked_at", 0))


def pop_pause(run_id: str) -> dict | None:
    data = get_pause(run_id)
    if data:
        _pause_path(run_id).unlink(missing_ok=True)
    return data


# -- Sub-agent wrapper  -- runs a sub-agent via ADK Runner ----------------

async def _run_sub_agent(
    agent: LlmAgent, message: str, result: OrchestratorResult, label: str,
    session_service, session_id, user_id, run_id: str | None = None,
) -> str:
    """Run one sub-agent turn. Emits agent_started/agent_finished around the call.

    Returns the final (non-partial) text output. Propagates exceptions loudly
    so the orchestrator's try/except can emit run_failed.
    """
    run_manager.manager.check()
    model_name = getattr(getattr(agent, "model", None), "model", "?")
    t0 = time.time()
    run_id = run_id or run_manager.manager.run_id
    emit("agent_started", {"agent": label, "model": model_name, "run_id": run_id})
    runner = Runner(agent=agent, session_service=session_service, app_name="venturebot")
    content = types.Content(role="user", parts=[types.Part(text=message)])
    events: list = []
    async for ev in runner.run_async(user_id=user_id, session_id=session_id, new_message=content):
        run_manager.manager.check()
        events.append(ev)
        t = _text_from_event(ev)
        if t and not ev.partial:
            result.events.append({"agent": label, "text": t})
            store.log(label, model_name, t[:200])
            agent_turn(label, t, run_id)
            capture_turn(session_id, label, "agent_message", t,
                        _THROTTLE.setdefault(session_id, {}))
    final = _final_text_of(events)
    if final:
        asyncio.create_task(_spawn_review_fork(session_id, final))
    duration = time.time() - t0
    emit("agent_finished", {"agent": label, "model": model_name, "duration": round(duration, 3), "run_id": run_id})
    return final


async def _spawn_review_fork(session_id: str, transcript: str) -> None:
    try:
        await asyncio.to_thread(
            analyze_turn, get_store(), transcript,
            _THROTTLE.setdefault(session_id, {}),
        )
    except Exception:
        pass


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


# -- Workspace file helpers ----------------------------------------------
# W4a (security review): workspaces are PER-RUN. A malicious user must not be
# able to steer an orchestrator (via prompt injection) into reading or writing
# another debate's files, so every file tool resolves strictly inside
# workspace/runs/{run_id}/  -- never in a shared global directory.

_RUN_ID_RE = re.compile(r"[^A-Za-z0-9_-]")


def _workspace_dir(run_id: str | None = None) -> Path:
    """Per-run workspace root. Unknown run_ids land in an isolated '_legacy' dir."""
    safe = _RUN_ID_RE.sub("_", run_id) if run_id else ""
    if not safe or safe in (".", ".."):
        safe = "_legacy"
    return config.WORKSPACE_DIR / "runs" / safe


def _resolve_in_workspace(run_id: str | None, rel_path: str) -> Path | None:
    """Resolve rel_path inside the run's workspace; None on traversal attempts."""
    root = _workspace_dir(run_id).resolve()
    try:
        candidate = (root / rel_path).resolve()
    except (OSError, ValueError):
        return None
    if not candidate.is_relative_to(root):
        return None  # path traversal (../, absolute path, symlink escape)
    return candidate


def _read_workspace_file(path: str, run_id: str | None = None) -> str | None:
    """Read a file from THIS run's workspace. Returns None if not found/blocked."""
    full = _resolve_in_workspace(run_id, path)
    if full is None:
        return None
    try:
        if full.is_file():
            return full.read_text(encoding="utf-8")
    except OSError:
        pass
    return None


def _write_workspace_file(path: str, content: str, run_id: str | None = None) -> str:
    """Write a file to THIS run's workspace. Returns 'ok' or error message."""
    ws = _workspace_dir(run_id)
    full = _resolve_in_workspace(run_id, path)
    if full is None:
        return "error: path escapes the run workspace (blocked)"
    try:
        ws.mkdir(parents=True, exist_ok=True)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        store.set_workspace_files([p.name for p in ws.glob("*")])
        return "ok"
    except OSError as e:
        return f"error: {e}"


# -- Quality gate: check if the orchestrator should stop ----------------

def _check_quality_gate(
    result: OrchestratorResult,
    turns_used: int,
    stall_count: int,
) -> tuple[bool, str]:
    """Returns (should_stop, reason). Called after each turn.

    Args:
        result: current orchestrator result
        turns_used: how many turns have run (1-based)
        stall_count: consecutive turns with no PRD change (computed by caller)
    """
    max_turns = config.ORCHESTRATOR_MAX_TURNS

    # If the human approved or rejected, always stop.
    if result.status in ("done", "stopped", "failed"):
        return True, f"run status is {result.status}"

    has_prd = bool(result.prd)
    has_verdict = bool(result.verdict)

    if has_prd and has_verdict:
        # Stall: PRD unchanged for too long  -> present what we have.
        if stall_count >= config.ORCHESTRATOR_STALL_TURNS:
            return True, f"PRD unchanged for {stall_count} turns  -- quality gate satisfied"

        # Near budget  -> present.
        if turns_used >= max_turns:
            return True, f"reached max turns ({max_turns}) with PRD + verdict"

        # Clean audit  -> present.
        if result.security_audit and result.security_audit.get("ok"):
            return True, "security audit passed  -- PRD is ready for approval"

    # Out of turns with no PRD  -> stop anyway.
    if turns_used >= max_turns:
        return True, f"reached max turns ({max_turns}) without completing PRD"

    return False, ""


# -- Orchestrator tool implementations (called by the orchestrator agent) -

class OrchestratorTools:
    """Function tool implementations for the orchestrator agent.

    Each method is a callable tool. They read/write `result` which is mutated
    in place by the orchestrator's drive loop.
    """

    def __init__(
        self,
        result: OrchestratorResult,
        session_service: InMemorySessionService,
        sid: str,
        user_id: str,
        inbox: SteeringInbox,
        run_id: str,
        agents: dict[str, LlmAgent] | None = None,
    ):
        self.result = result
        self.session_service = session_service
        self.sid = sid
        self.user_id = user_id
        self.inbox = inbox
        self.run_id = run_id
        self.agents = agents if agents is not None else ALL_AGENTS

    async def load_memories(self) -> str:
        """Load past lessons and techniques from VentureBot's memory store.

        Call this FIRST, before any other tool. The orchestrator's system prompt
        says to apply ALL returned lessons  -- they exist because past runs made
        mistakes that must not be repeated.
        """
        try:
            s = get_store()
            lessons = s.get_lessons(active_only=True, limit=20)
            techniques = s.get_techniques(active_only=True)
        except Exception:
            return "(memory store unavailable)"

        if not lessons and not techniques:
            return "No past lessons found. This is a fresh start."

        parts = ["## Past Lessons (MUST APPLY)"]
        for l in lessons:
            parts.append(f"- {l['name']}: {l['rule']}")
        if techniques:
            parts.append("\n## Active Techniques")
            for t in techniques:
                parts.append(f"- {t['name']}: {t['description']}")
        return "\n".join(parts)

    async def research(self, idea: str) -> str:
        """Research an idea. Returns a structured research brief."""
        store.update_task("t1", "in_progress")
        emit("phase_started", {"phase": "research", "agent": "Researcher", "run_id": self.run_id})

        urls = self.inbox.drain_urls()
        url_digest = ""
        if urls:
            from ..url_fetch import fetch_urls
            store.log("System", "core", f"Fetching {len(urls)} user-provided URL(s)...")
            url_digest = fetch_urls(urls)

        steering = self.inbox.drain_steering()
        steering_block = ""
        if steering:
            steering_block = "\n\nUSER STEERING:\n" + "\n".join(f"- {s}" for s in steering)

        msg = f"Research this idea: {idea}"
        if url_digest:
            msg += f"\n\nThe user has provided these research URLs:\n{url_digest}"
        msg += steering_block

        brief = await _run_sub_agent(
            self.agents["researcher"], msg, self.result, "Researcher",
            self.session_service, self.sid, self.user_id, run_id=self.run_id,
        )
        self.result.research_brief = brief
        store.update_task("t1", "done")
        emit("phase_done", {"phase": "research", "run_id": self.run_id})
        return brief

    async def advocate(self) -> str:
        """Argue FOR the idea. The Advocate is BLIND  -- it has no web search."""
        store.update_task("t2", "in_progress")
        emit("phase_started", {"phase": "advocate", "agent": "Advocate", "run_id": self.run_id})

        brief = self.result.research_brief or "(no research brief available)"
        argument = await _run_sub_agent(
            self.agents["advocate"],
            f"Research Brief:\n\n{brief}\n\nArgue FOR this idea.",
            self.result, "Advocate",
            self.session_service, self.sid, self.user_id, run_id=self.run_id,
        )
        self.result.advocate_argument = argument
        store.update_task("t2", "done")
        emit("phase_done", {"phase": "advocate", "run_id": self.run_id})
        return argument

    async def critic(self) -> str:
        """Challenge every claim. The Critic HAS web search for counter-evidence."""
        store.update_task("t3", "in_progress")
        emit("phase_started", {"phase": "critic", "agent": "Critic", "run_id": self.run_id})

        brief = self.result.research_brief or "(no brief)"
        argument = self.result.advocate_argument or "(no argument)"
        rebuttal = await _run_sub_agent(
            self.agents["critic"],
            f"Research Brief:\n\n{brief}\n\nAdvocate's Argument:\n\n{argument}\n\nChallenge every claim.",
            self.result, "Critic",
            self.session_service, self.sid, self.user_id, run_id=self.run_id,
        )
        self.result.critic_rebuttal = rebuttal
        store.update_task("t3", "done")
        emit("phase_done", {"phase": "critic", "run_id": self.run_id})
        return rebuttal

    async def creative(self) -> str:
        """Find niches, pivots, unfair advantages  -- divergent, high-temperature."""
        emit("phase_started", {"phase": "creative", "agent": "Creative", "run_id": self.run_id})

        brief = self.result.research_brief or "(no brief)"
        argument = self.result.advocate_argument or "(no argument)"
        rebuttal = self.result.critic_rebuttal or "(no rebuttal)"
        angles = await _run_sub_agent(
            self.agents["creative"],
            f"Research Brief:\n\n{brief}\n\nAdvocate:\n\n{argument}\n\nCritic's challenges:\n\n{rebuttal}\n\nFind the niche, pivots, unfair advantages and wild ideas.",
            self.result, "Creative",
            self.session_service, self.sid, self.user_id, run_id=self.run_id,
        )
        self.result.creative_angles = angles
        emit("phase_done", {"phase": "creative", "run_id": self.run_id})
        return angles

    async def judge(self) -> str:
        """Produce a structured verdict with scores."""
        store.update_task("t4", "in_progress")
        emit("phase_started", {"phase": "judge", "agent": "Judge", "run_id": self.run_id})

        brief = self.result.research_brief or "(no brief)"
        argument = self.result.advocate_argument or "(no argument)"
        rebuttal = self.result.critic_rebuttal or "(no rebuttal)"
        angles = self.result.creative_angles or "(no creative angles)"

        verdict_text = await _run_sub_agent(
            self.agents["judge"],
            f"Research Brief:\n\n{brief}\n\nAdvocate:\n\n{argument}\n\nCritic:\n\n{rebuttal}\n\nCreative angles:\n\n{angles}\n\nProduce your structured verdict.",
            self.result, "Judge",
            self.session_service, self.sid, self.user_id, run_id=self.run_id,
        )
        self.result.verdict_text = verdict_text
        self.result.verdict = _parse_verdict(verdict_text)
        store.update_task("t4", "done")
        emit("verdict", {
            "verdict": self.result.verdict.get("verdict", "PARK"),
            "verdict_text": verdict_text,
            "scores": self.result.verdict.get("scores", {}),
            "key_risks": self.result.verdict.get("key_risks", []),
            "run_id": self.run_id,
        })
        emit("phase_done", {"phase": "judge", "run_id": self.run_id})
        return verdict_text

    async def write_prd(self, instructions: str = "") -> str:
        """Write or revise the PRD. Pass instructions to guide revisions."""
        store.update_task("t5", "in_progress")
        emit("phase_started", {"phase": "prd_writer", "agent": "PRD Writer", "run_id": self.run_id})

        brief = self.result.research_brief or "(no brief)"
        argument = self.result.advocate_argument or "(no argument)"
        rebuttal = self.result.critic_rebuttal or "(no rebuttal)"
        angles = self.result.creative_angles or "(no angles)"
        verdict_text = self.result.verdict_text or "(no verdict)"

        msg = (
            f"Research Brief:\n\n{brief}\n\n"
            f"Advocate:\n\n{argument}\n\n"
            f"Critic:\n\n{rebuttal}\n\n"
            f"Creative angles:\n\n{angles}\n\n"
            f"Verdict:\n\n{verdict_text}\n\n"
            f"Write the PRD."
        )
        if instructions:
            msg += f"\n\nREVISION INSTRUCTIONS:\n{instructions}\n\nRe-read the existing PRD and apply these changes."

        prd = await _run_sub_agent(
            self.agents["prd_writer"], msg, self.result, "PRD Writer",
            self.session_service, self.sid, self.user_id, run_id=self.run_id,
        )
        self.result.prd = prd
        _write_workspace_file("PRD.md", prd, run_id=self.run_id)
        store.update_task("t5", "done")
        emit("phase_done", {"phase": "prd_writer", "run_id": self.run_id})
        return prd

    async def audit(self) -> str:
        """Proof-read the PRD. Returns PASS or FLAG with findings."""
        emit("phase_started", {"phase": "auditor", "agent": "Security Auditor", "run_id": self.run_id})

        brief = self.result.research_brief or "(no brief)"
        prd = self.result.prd or "(no PRD)"

        from ..artifact_scanner import proof_read_gate, scan_artifact
        scan = scan_artifact(prd, kind="text")

        audit_text = await _run_sub_agent(
            self.agents["auditor"],
            f"Proof-read this PRD.\n\nRESEARCH BRIEF:\n{brief}\n\nPRD:\n{prd}\n\nReturn your structured verdict.",
            self.result, "Security Auditor",
            self.session_service, self.sid, self.user_id, run_id=self.run_id,
        )
        audit = _parse_audit(audit_text)
        self.result.security_audit = proof_read_gate(
            scanner_ok=scan.ok,
            audit_verdict=audit.get("verdict") if audit else None,
            findings=[f.to_dict() for f in scan.findings]
                      + (audit.get("findings", []) if audit else []),
        )
        emit("phase_done", {"phase": "auditor", "run_id": self.run_id})
        return audit_text

    async def scan_prd(self) -> str:
        """Run the deterministic PRD completeness scanner.
        
        Checks that the PRD has all required sections, acceptance criteria,
        security coverage, and sourced claims. Returns PASS or FLAG with findings.
        Call this AFTER write_prd() and BEFORE presenting to the human.
        If FLAG, call write_prd() with instructions to fix the issues found.
        """
        from ..prd_scanner import scan_prd as _scan, format_scan_result
        
        prd = self.result.prd
        if not prd:
            return "No PRD available yet. Call write_prd() first."
        
        emit("phase_started", {"phase": "prd_scanner", "run_id": self.run_id})
        result = _scan(prd)
        emit("phase_done", {"phase": "prd_scanner", "verdict": result.verdict, "run_id": self.run_id})
        
        store.log("PRD Scanner", "core", f"Scan: {result.verdict} ({len(result.findings)} findings)")
        return format_scan_result(result)

    async def read_file(self, path: str) -> str:
        """Read a file from the workspace. Call before editing any file."""
        content = _read_workspace_file(path, run_id=self.run_id)
        if content is None:
            return f"File not found: {path}"
        return content

    async def write_file(self, path: str, content: str) -> str:
        """Write a file to the workspace."""
        return _write_workspace_file(path, content, run_id=self.run_id)

    async def save_artifact(self, path: str) -> str:
        """Save a workspace file as an artifact for the user to download."""
        content = _read_workspace_file(path, run_id=self.run_id)
        if content is None:
            return f"File not found: {path}"
        # Store the artifact reference in the result for the dashboard
        return f"Artifact '{path}' saved."

    async def clarify(self, question: str) -> str:
        """Ask the human a clarifying question. PAUSES the debate durably.

        Use when the idea is vague, research is contradictory, you need domain
        expertise, or you're presenting results for approval.

        The full debate state is persisted to disk and this run ENDS cleanly;
        answering (any time later, even after a server restart) starts a
        continuation run that resumes from the snapshot. No timeouts  -- the
        human may be at lunch or come back next week.
        """
        self.result.clarification_question = question
        self.result.clarification_state = "awaiting_response"
        emit("clarify_question", {
            "question": question,
            "run_id": self.run_id,
        })
        emit("clarify", {
            "question": question,
            "run_id": self.run_id,
        })
        store.log("Orchestrator", "core", f"Clarify: {question[:200]}")

        # Persist BEFORE raising so even a crash right after keeps state.
        persist_pause(self.result, self.run_id)
        raise ClarifyPaused(question)


# -- Verdict + audit parsers --------------------------------------------

def _parse_verdict(text: str) -> dict:
    """Parse the Judge's raw output into a verdict dict."""
    import re
    if not text:
        return {"verdict": "PARK", "error": "no output"}
    try:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, dict) and parsed.get("verdict") in ("PROCEED", "PARK", "PRUNE"):
                return parsed
    except json.JSONDecodeError:
        pass
    upper = text.upper()
    for kw in ("PROCEED", "PARK", "PRUNE"):
        if kw in upper:
            return {"verdict": kw}
    return {"verdict": "PARK", "error": "could not parse verdict"}


def _parse_audit(text: str) -> dict:
    """Parse the Security Auditor's output."""
    import re
    if not text:
        return {}
    try:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, dict):
                return parsed
    except json.JSONDecodeError:
        pass
    upper = text.upper()
    if "PASS" in upper and "FLAG" not in upper:
        return {"verdict": "PASS", "findings": []}
    if "FLAG" in upper:
        return {"verdict": "FLAG", "findings": []}
    return {}


# -- Main orchestrator run ----------------------------------------------

async def run_orchestrator(
    idea: str,
    *,
    inbox: SteeringInbox | None = None,
    session_id: str | None = None,
    resume_idea_id: str | None = None,
    resume_comment: str | None = None,
    paused_state: dict | None = None,
    clarify_answer: str | None = None,
    api_key: str | None = None,
    external_run_id: str | None = None,
) -> OrchestratorResult:
    """Run the autonomous orchestrator loop for one idea.

    The orchestrator is an ADK LlmAgent that calls sub-agent tools. We drive it
    in a managed loop with a quality gate and iteration budget.

    If resume_idea_id is provided, loads previous context (research, debate,
    verdict, PRD) from that idea and injects it into the orchestrator's initial
    state so it can continue from where it left off.

    resume_comment is the human's new input on a resumed run ("what changed",
    new direction, feedback)  -- injected into the first turn prompt and stored
    with the run history.

    paused_state + clarify_answer resume a durably-paused clarification: all
    fields are restored from the pause snapshot and the human's answer is
    injected into the first turn prompt. Works across server restarts.

    Returns an OrchestratorResult with the full debate transcript, verdict,
    PRD, and security audit.
    """
    from ..input_guard import guard_input

    inbox = inbox or SteeringInbox()
    result = OrchestratorResult(idea=idea)

    if paused_state:
        # Restore the debate exactly as it was when the question was asked.
        result.idea = paused_state.get("idea") or idea
        result.idea_id = paused_state.get("idea_id")
        result.idea_run_id = paused_state.get("idea_run_id")
        result.resume_comment = paused_state.get("resume_comment")
        result.turns_used = int(paused_state.get("turns_used") or 0)
        result.research_brief = paused_state.get("research_brief")
        result.advocate_argument = paused_state.get("advocate_argument")
        result.critic_rebuttal = paused_state.get("critic_rebuttal")
        result.creative_angles = paused_state.get("creative_angles")
        result.verdict_text = paused_state.get("verdict_text")
        result.verdict = paused_state.get("verdict")
        result.prd = paused_state.get("prd")
        result.security_audit = paused_state.get("security_audit")
        result.events = list(paused_state.get("events") or [])
        result.clarification_answer = clarify_answer
        result.clarification_question = paused_state.get("question")

    run_id = external_run_id or store.start_run()["run_id"]
    run_manager.manager.start(run_id)

    # Record or resume the idea in the idea tree
    try:
        store_obj = get_store()
        if paused_state:
            # Continuation of a paused debate  -- idea row already exists.
            pass
        elif resume_idea_id:
            # Resume existing idea  -- load previous context
            result.idea_id = resume_idea_id
            prev = store_obj.get_idea(resume_idea_id)
            if prev:
                result.research_brief = prev.get("research_brief")
                result.verdict_text = prev.get("verdict")
                result.prd = prev.get("prd_text")
                if prev.get("scores"):
                    try:
                        import json
                        result.verdict = json.loads(prev["scores"])
                    except Exception:
                        pass
                store_obj.update_idea_content(resume_idea_id, workspace_path=f"runs/{run_id}/")
        else:
            # New idea  -- keep the FULL pitch text, not just the truncated title.
            result.idea_id = store_obj.create_idea(idea[:200], description=idea)
            store_obj.update_idea_content(result.idea_id, workspace_path=f"runs/{run_id}/")
        # Open the per-run snapshot row (P1.1): every debate gets its own
        # immutable history entry  -- past debates are never overwritten.
        if result.idea_id:
            result.resume_comment = (resume_comment or "").strip() or None
            result.idea_run_id = store_obj.start_idea_run(result.idea_id, comment=result.resume_comment)
    except Exception:
        pass

    # Input guard
    guarded = guard_input(idea)
    if guarded["blocked"]:
        result.status = "failed"
        result.error = f"Input blocked: {guarded['matches'][:3]}"
        store.set_status("failed")
        _archive_result(result, run_id)
        return result

    # Build the orchestrator agent
    # Create agents with custom API key if provided (BYOK), otherwise use defaults
    agents = create_agents(api_key) if api_key else ALL_AGENTS
    
    tools_obj = OrchestratorTools(result, InMemorySessionService(), "", "user", inbox, run_id, agents)
    session_service = InMemorySessionService()
    sid = session_id or (await session_service.create_session(app_name="venturebot", user_id="user")).id
    tools_obj.session_service = session_service
    tools_obj.sid = sid
    _RUNS[run_id] = result

    # Store tools_obj on the result so the dashboard can reach clarify
    result._tools = tools_obj  # type: ignore[attr-defined]

    # Build tools list  -- ADK function tools
    from google.adk.tools import FunctionTool

    orchestrator_agent = LlmAgent(
        name="orchestrator",
        model=Gemini(model=config.MODEL_ORCHESTRATOR, client_kwargs={'api_key': api_key}) if api_key else Gemini(model=config.MODEL_ORCHESTRATOR),
        instruction=_orchestrator_instruction(),
        tools=[
            FunctionTool(tools_obj.load_memories),
            FunctionTool(tools_obj.research),
            FunctionTool(tools_obj.advocate),
            FunctionTool(tools_obj.critic),
            FunctionTool(tools_obj.creative),
            FunctionTool(tools_obj.judge),
            FunctionTool(tools_obj.write_prd),
            FunctionTool(tools_obj.audit),
            FunctionTool(tools_obj.scan_prd),
            FunctionTool(tools_obj.read_file),
            FunctionTool(tools_obj.write_file),
            FunctionTool(tools_obj.save_artifact),
            FunctionTool(tools_obj.clarify),
        ],
        description="Idea Lint Orchestrator  -- researches, debates, and produces PRDs autonomously.",
    )

    # Drive loop
    turns_used = 0
    max_turns = config.ORCHESTRATOR_MAX_TURNS
    max_tool_calls = config.ORCHESTRATOR_MAX_TOOL_CALLS
    last_prd: str | None = None
    stall_count = 0

    store.log("System", "core", f"Orchestrator starting: '{idea[:120]}'")
    emit("run_started", {"idea": idea, "run_id": run_id})
    if result.resume_comment:
        # The human's new input is part of the debate record  -- show it in the
        # live feed AND persist it as the first transcript entry.
        result.events.append({"agent": "Human", "text": result.resume_comment})
        store.log("Human", "user", result.resume_comment[:200])
        agent_turn("Human", result.resume_comment, run_id)
        emit("human_comment", {"idea_id": result.idea_id, "comment": result.resume_comment})

    try:
        while turns_used < max_turns:
            run_manager.manager.check()

            # Check quality gate before each turn
            should_stop, reason = _check_quality_gate(result, turns_used, stall_count)
            if should_stop:
                store.log("System", "core", f"Quality gate: {reason}")
                # Surface the stopping decision as a first-class UI event  -- 
                # the orchestrator is a debate participant too.
                emit("orchestrator_decision", {
                    "decision": "stop",
                    "reason": reason,
                    "turns_used": turns_used,
                    "max_turns": max_turns,
                    "run_id": run_id,
                })
                break

            # Decision point: the orchestrator keeps the loop going.
            emit("orchestrator_decision", {
                "decision": "continue",
                "reason": f"starting turn {turns_used + 1} of {max_turns}",
                "turns_used": turns_used,
                "max_turns": max_turns,
                "run_id": run_id,
            })

            # Build the prompt for this turn
            turn_prompt = _build_turn_prompt(result, turns_used, max_turns)

            # Run one orchestrator turn
            runner = Runner(agent=orchestrator_agent, session_service=session_service, app_name="venturebot")
            content = types.Content(role="user", parts=[types.Part(text=turn_prompt)])

            tool_calls_this_turn = 0
            async for ev in runner.run_async(user_id="user", session_id=sid, new_message=content):
                run_manager.manager.check()
                tool_calls_this_turn += 1
                if tool_calls_this_turn >= max_tool_calls:
                    store.log("System", "core", f"Tool call limit ({max_tool_calls}) reached this turn")

                t = _text_from_event(ev)
                if t and not ev.partial:
                    result.events.append({"agent": "Orchestrator", "text": t})
                    store.log("Orchestrator", config.MODEL_ORCHESTRATOR, t[:200])
                    agent_turn("Orchestrator", t, run_id)

            turns_used += 1
            store.set_iteration(turns_used)

            # Update stall tracking
            if result.prd is not None:
                if result.prd == last_prd:
                    stall_count += 1
                else:
                    stall_count = 0
                    last_prd = result.prd

            # Check quality gate after each turn
            should_stop, reason = _check_quality_gate(result, turns_used, stall_count)
            if should_stop:
                store.log("System", "core", f"Quality gate after turn {turns_used}: {reason}")
                emit("orchestrator_decision", {
                    "decision": "stop",
                    "reason": reason,
                    "turns_used": turns_used,
                    "max_turns": max_turns,
                    "run_id": run_id,
                })
                break

        result.turns_used = turns_used

        # If we have a PRD but no security audit yet, run it now
        if result.prd and not result.security_audit:
            store.log("System", "core", "Auto-running security audit before presenting...")
            try:
                audit_text = await tools_obj.audit()
                store.log("Security Auditor", config.MODEL_AUDITOR, audit_text[:200])
            except Exception:
                pass

        # Final status
        if result.status == "running":
            if result.prd and result.verdict:
                avg = _overall_average(result.verdict)
                v = result.verdict.get("verdict", "PARK")
                if v == "PROCEED" or (avg is not None and avg >= 7):
                    # PRD is ready, but we need human approval
                    result.status = "needs_approval"
                    store.log("System", "core", f"Orchestrator complete: PRD ready (avg {avg})  -- awaiting human approval")
                else:
                    result.status = "needs_verdict"
                    store.log("System", "core", f"Orchestrator complete: verdict {v} (avg {avg})  -- awaiting human decision")
            else:
                result.status = "needs_verdict"
                store.log("System", "core", "Orchestrator complete: incomplete  -- awaiting human decision")

        emit("run_finished", {
            "status": result.status,
            "verdict": result.verdict,
            "creative_angles": result.creative_angles,
            "has_prd": bool(result.prd),
            "prd": result.prd,
            "security_audit": result.security_audit,
            "turns_used": turns_used,
            "error": result.error,
            "run_id": run_id,
        })

    except run_manager.RunCancelled:
        result.status = "stopped"
        store.set_status("stopped")
        _archive_result(result, run_id)
    except ClarifyPaused:
        # Durable pause: state is already persisted to disk by clarify().
        result.status = "needs_clarification"
        store.set_status("waiting_user")
        store.log("System", "core",
                  f"Debate paused -- waiting for your answer (no time limit). Run {run_id}")
        emit("clarify_question", {
            "run_id": run_id,
            "question": result.clarification_question,
            "idea_id": result.idea_id,
        })
        emit("run_paused", {
            "run_id": run_id,
            "question": result.clarification_question,
            "idea_id": result.idea_id,
        })
    except Exception as e:
        # ADK may wrap tool exceptions inside DynamicNodeFailError or runner exceptions
        cause = getattr(e, "error", None) or getattr(e, "__cause__", None) or getattr(e, "__context__", None)
        is_clarify = (
            isinstance(e, ClarifyPaused)
            or isinstance(cause, ClarifyPaused)
            or "ClarifyPaused" in type(e).__name__
            or (cause and "ClarifyPaused" in type(cause).__name__)
            or bool(result.clarification_question)
        )
        if is_clarify:
            result.status = "needs_clarification"
            store.set_status("waiting_user")
            store.log("System", "core",
                      f"Debate paused -- waiting for your answer (no time limit). Run {run_id}")
            emit("clarify_question", {
                "run_id": run_id,
                "question": result.clarification_question,
                "idea_id": result.idea_id,
            })
            emit("run_paused", {
                "run_id": run_id,
                "question": result.clarification_question,
                "idea_id": result.idea_id,
            })
        else:
            result.status = "failed"
            result.error = f"{type(e).__name__}: {e}"
            store.set_status("failed")
            store.log("System", "core", f"Orchestrator failed: {result.error}")
            # Loud failure (T2): the UI must get an explicit run_failed with a reason.
            emit("run_failed", {"reason": result.error, "run_id": run_id})
            _archive_result(result, run_id)

    _RUNS.pop(run_id, None)
    return result


def _build_turn_prompt(result: OrchestratorResult, turns_used: int, max_turns: int) -> str:
    """Build the prompt the orchestrator sees at the start of each turn."""
    parts = []

    # State summary
    parts.append(f"## Turn {turns_used + 1} of {max_turns}")

    # The idea itself  -- ALWAYS visible, every turn. Session history can be
    # truncated/lost (e.g. after a clarify timeout the loop keeps going), and
    # without it the orchestrator hallucinates an idea from memory lessons.
    parts.append(f"\n## THE IDEA TO EVALUATE:\n{result.idea}")

    # The human's answer to the question that paused this debate  -- injected
    # into the FIRST turn after resume only.
    if turns_used == 0 and result.clarification_answer:
        parts.append(
            "\n## HUMAN ANSWER TO YOUR QUESTION\n"
            "You asked (the debate then paused, possibly for hours or days):\n"
            f"{(result.clarification_question or '(question)')[:500]}\n\n"
            "The human answered:\n"
            f"{result.clarification_answer}\n\n"
            "Continue from where you left off using this answer. Do NOT restart "
            "the research from scratch unless the answer invalidates it."
        )

    # If resuming with previous context, show it
    if turns_used == 0 and (result.research_brief or result.prd):
        parts.append("\n## RESUMED FROM PREVIOUS RUN")
        parts.append("This idea was previously evaluated. Here's what was done:")
        if result.research_brief:
            parts.append(f"\n### Previous Research Brief (first 500 chars):\n{result.research_brief[:500]}...")
        if result.verdict_text:
            parts.append(f"\n### Previous Verdict:\n{result.verdict_text[:300]}...")
        if result.prd:
            parts.append(f"\n### Previous PRD (first 500 chars):\n{result.prd[:500]}...")
        parts.append("\nYou can continue refining this work, or start fresh if the user provides new direction. Call research() again if you need updated information.")

    # The human's new input on a resumed run  -- highest-priority guidance.
    if turns_used == 0 and result.resume_comment:
        parts.append(f"\n## HUMAN COMMENT (new direction from the user):\n{result.resume_comment}")
        parts.append("Address this comment first. It reflects what changed since the last run (new evidence, new thoughts, market shifts, or feedback on the previous verdict/PRD).")

    progress = []
    if result.research_brief:
        progress.append("✅ Research done")
    else:
        progress.append("⬜ Research needed")
    if result.advocate_argument:
        progress.append("✅ Advocate argued")
    if result.critic_rebuttal:
        progress.append("✅ Critic challenged")
    if result.creative_angles:
        progress.append("✅ Creative explored")
    if result.verdict:
        v = result.verdict.get("verdict", "?")
        progress.append(f"✅ Judge verdict: {v}")
    else:
        progress.append("⬜ Judge needed")
    if result.prd:
        progress.append("✅ PRD drafted")
    else:
        progress.append("⬜ PRD needed")
    if result.security_audit:
        s = "PASS" if result.security_audit.get("ok") else "FLAG"
        progress.append(f"✅ Audit: {s}")
    else:
        progress.append("⬜ Audit needed")
    parts.append("Progress: " + " | ".join(progress))

    if result.clarification_question and not result.clarification_answer:
        parts.append(f"\n⚠️  Pending clarification: {result.clarification_question}")

    parts.append("\nTake the NEXT step in the engineering process. If you have a PRD and audit, and they are clean  -- present to the human. If the PRD needs revision, call write_prd() with specific instructions. If you need information, call research() or clarify().")

    return "\n".join(parts)


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


def _archive_result(result: OrchestratorResult, run_id: str) -> None:
    """Persist the result to the idea tree + close the per-run snapshot."""
    try:
        s = get_store()
        idea_id = result.idea_id
        if not idea_id:
            rows = s.get_idea_tree()
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
        # Close the per-run snapshot row (P1.1) so this debate stays replayable.
        if result.idea_run_id:
            s.finish_idea_run(
                result.idea_run_id,
                status=result.status,
                verdict=(result.verdict or {}).get("verdict"),
                scores=scores,
                research_brief=result.research_brief,
                debate_transcript=json.dumps(result.events),
                prd_text=result.prd,
                turns_used=result.turns_used or None,
            )
    except Exception:
        pass


def get_run(run_id: str) -> OrchestratorResult | None:
    """Get a running orchestrator result by run_id (for the dashboard)."""
    return _RUNS.get(run_id)


def answer_clarify(run_id: str, answer: str) -> dict | None:
    """Answer a durably-paused clarification.

    Pops the pause snapshot (works across server restarts). Returns the pause
    record for the caller to spawn a continuation run with, or None if there
    is no pending pause under this run_id.
    """
    return pop_pause(run_id)