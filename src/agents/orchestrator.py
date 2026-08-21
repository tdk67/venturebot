"""Orchestrator — autonomous agentic loop that drives the debate sub-agents.

The Orchestrator is a single ADK LlmAgent with function tools that wrap each
sub-agent (Researcher, Advocate, Critic, Creative, Judge, PRD Writer, Auditor).
It runs in a loop with an iteration budget and quality-gate stopping rules.

Design:
  - The orchestrator decides WHAT to do next — the system prompt encodes the
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
import time
from dataclasses import dataclass, field
from pathlib import Path

from google.adk import Runner
from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .. import config, run_manager, store
from ..events import agent_turn, emit
from ..memory.auto_capture import capture_turn
from ..memory.review_fork import analyze_turn
from ..memory.sqlite_store import get_store
from ..steering import SteeringInbox
from .agents import ALL_AGENTS

logger = logging.getLogger(__name__)

# ── Persisted run result (exposed to dashboard) ────────────────────────
_RUNS: dict[str, "OrchestratorResult"] = {}

# Throttle state for auto_capture fork
_THROTTLE: dict[str, dict] = {}

# ── Orchestrator system prompt ──────────────────────────────────────────

_ORCHESTRATOR_PROMPT = """You are VentureBot's Orchestrator. Your job is to evaluate a startup idea
through a rigorous multi-agent engineering process. You don't debate yourself —
you delegate to specialized sub-agents, each with a different perspective
and (where appropriate) different information access.

## YOUR PROCESS

Follow this engineering process. You may loop back at any point if you discover
gaps or the human provides new information.

### 1. LOAD PAST LESSONS
Before anything else, call load_memories(). This returns lessons VentureBot
learned from previous runs. APPLY ALL OF THESE. If a lesson says "always run
security audit before presenting", you MUST run the audit. If a lesson says
"verify market size claims with search", you MUST do that.

### 2. RESEARCH
Call research(idea) to get a structured brief with prior art, market signals,
technical landscape, and resource links. If the user provided URLs, pass them.

### 3. CLARIFY
If the idea is vague, or the research reveals contradictory information, or you
need domain expertise the user might have, call clarify(question). Wait for the
answer — this tool PAUSES until the human responds. Then re-research with the
new information. You may clarify multiple times if needed.

### 4. DEBATE
Call advocate(), then critic(), then creative(). The Advocate is BLIND (no web
search) — it argues from the brief alone. The Critic HAS web search — it finds
counter-evidence. The Creative finds niches, pivots, and unfair advantages.

### 5. JUDGE
Call judge(). It returns a structured verdict with scores:
- Novelty (1-10), Feasibility (1-10), Market Fit (1-10)
- Overall: PROCEED (avg >=7), PARK (4-6), PRUNE (<4)

### 6. VERDICT GATE
- PROCEED: continue to PRD drafting.
- PARK or PRUNE: present the verdict to the human via clarify() — ask whether
  to proceed anyway, park, or abandon.

### 7. DRAFT PRD
Call write_prd(). This writes a structured PRD to the workspace.

### 8. SELF-REVIEW — CRITICAL QUALITY GATE
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
- If Reject: stop — the run is done.
- If Approve: stop — the run is a success.

## STOPPING RULES

You MUST stop and present whatever you have when ANY of these is true:
1. The human has approved or rejected → done.
2. You've used 10 turns AND have a PRD + verdict → present, don't loop forever.
3. You've made NO progress for 3 consecutive turns (same file contents, no
   new information gathered, no new quality issues found) → present what you
   have and explain what's missing.
4. If you're genuinely stuck — clarify() is always available. Never loop in
   confusion.

## WORKSPACE DISCIPLINE

- Write files to the workspace (RESEARCH_BRIEF.md, PRD.md).
- ALWAYS call read_file() to re-read a file before editing it.
- NEVER edit from memory — stale edits corrupt the artifact.
- After the human approves, save the PRD with save_artifact().

## PAST LESSONS (from load_memories())

These are lessons from previous runs. You MUST apply them — they exist because
the human or the system flagged a mistake that must not be repeated.

{must_read_before_starting}

---

Now begin: evaluate the user's idea following this process. Start by calling
load_memories(), then research().
"""


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


# ── Sub-agent wrapper — runs a sub-agent via ADK Runner ────────────────

async def _run_sub_agent(
    agent: LlmAgent, message: str, result: OrchestratorResult, label: str,
    session_service, session_id, user_id,
) -> str:
    """Run one sub-agent turn. Returns the final (non-partial) text output."""
    run_manager.manager.check()
    runner = Runner(agent=agent, session_service=session_service, app_name="venturebot")
    content = types.Content(role="user", parts=[types.Part(text=message)])
    events: list = []
    async for ev in runner.run_async(user_id=user_id, session_id=session_id, new_message=content):
        run_manager.manager.check()
        events.append(ev)
        t = _text_from_event(ev)
        if t and not ev.partial:
            result.events.append({"agent": label, "text": t})
            store.log(label, getattr(agent.model, "model", "?"), t[:200])
            agent_turn(label, t, run_manager.manager.run_id)
            capture_turn(session_id, label, "agent_message", t,
                         _THROTTLE.setdefault(session_id, {}))
    final = _final_text_of(events)
    if final:
        asyncio.create_task(_spawn_review_fork(session_id, final))
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


# ── Workspace file helpers ──────────────────────────────────────────────

def _workspace_dir() -> Path:
    return config.WORKSPACE_DIR


def _read_workspace_file(path: str) -> str | None:
    """Read a file from the workspace. Returns None if not found."""
    full = _workspace_dir() / path
    try:
        if full.is_file() and full.exists():
            return full.read_text()
    except OSError:
        pass
    return None


def _write_workspace_file(path: str, content: str) -> str:
    """Write a file to the workspace. Returns 'ok' or error message."""
    _workspace_dir().mkdir(parents=True, exist_ok=True)
    full = _workspace_dir() / path
    try:
        full.write_text(content)
        store.set_workspace_files([p.name for p in _workspace_dir().glob("*")])
        return "ok"
    except OSError as e:
        return f"error: {e}"


# ── Quality gate: check if the orchestrator should stop ────────────────

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
        # Stall: PRD unchanged for too long → present what we have.
        if stall_count >= config.ORCHESTRATOR_STALL_TURNS:
            return True, f"PRD unchanged for {stall_count} turns — quality gate satisfied"

        # Near budget → present.
        if turns_used >= max_turns:
            return True, f"reached max turns ({max_turns}) with PRD + verdict"

        # Clean audit → present.
        if result.security_audit and result.security_audit.get("ok"):
            return True, "security audit passed — PRD is ready for approval"

    # Out of turns with no PRD → stop anyway.
    if turns_used >= max_turns:
        return True, f"reached max turns ({max_turns}) without completing PRD"

    return False, ""


# ── Orchestrator tool implementations (called by the orchestrator agent) ─

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
    ):
        self.result = result
        self.session_service = session_service
        self.sid = sid
        self.user_id = user_id
        self.inbox = inbox
        self.run_id = run_id
        self._clarify_answered_event: asyncio.Event | None = None
        self._clarify_answer: str | None = None

    async def load_memories(self) -> str:
        """Load past lessons and techniques from VentureBot's memory store.

        Call this FIRST, before any other tool. The orchestrator's system prompt
        says to apply ALL returned lessons — they exist because past runs made
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
            ALL_AGENTS["researcher"], msg, self.result, "Researcher",
            self.session_service, self.sid, self.user_id,
        )
        self.result.research_brief = brief
        store.update_task("t1", "done")
        emit("phase_done", {"phase": "research", "run_id": self.run_id})
        return brief

    async def advocate(self) -> str:
        """Argue FOR the idea. The Advocate is BLIND — it has no web search."""
        store.update_task("t2", "in_progress")
        emit("phase_started", {"phase": "advocate", "agent": "Advocate", "run_id": self.run_id})

        brief = self.result.research_brief or "(no research brief available)"
        argument = await _run_sub_agent(
            ALL_AGENTS["advocate"],
            f"Research Brief:\n\n{brief}\n\nArgue FOR this idea.",
            self.result, "Advocate",
            self.session_service, self.sid, self.user_id,
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
            ALL_AGENTS["critic"],
            f"Research Brief:\n\n{brief}\n\nAdvocate's Argument:\n\n{argument}\n\nChallenge every claim.",
            self.result, "Critic",
            self.session_service, self.sid, self.user_id,
        )
        self.result.critic_rebuttal = rebuttal
        store.update_task("t3", "done")
        emit("phase_done", {"phase": "critic", "run_id": self.run_id})
        return rebuttal

    async def creative(self) -> str:
        """Find niches, pivots, unfair advantages — divergent, high-temperature."""
        emit("phase_started", {"phase": "creative", "agent": "Creative", "run_id": self.run_id})

        brief = self.result.research_brief or "(no brief)"
        argument = self.result.advocate_argument or "(no argument)"
        rebuttal = self.result.critic_rebuttal or "(no rebuttal)"
        angles = await _run_sub_agent(
            ALL_AGENTS["creative"],
            f"Research Brief:\n\n{brief}\n\nAdvocate:\n\n{argument}\n\nCritic's challenges:\n\n{rebuttal}\n\nFind the niche, pivots, unfair advantages and wild ideas.",
            self.result, "Creative",
            self.session_service, self.sid, self.user_id,
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
            ALL_AGENTS["judge"],
            f"Research Brief:\n\n{brief}\n\nAdvocate:\n\n{argument}\n\nCritic:\n\n{rebuttal}\n\nCreative angles:\n\n{angles}\n\nProduce your structured verdict.",
            self.result, "Judge",
            self.session_service, self.sid, self.user_id,
        )
        self.result.verdict_text = verdict_text
        self.result.verdict = _parse_verdict(verdict_text)
        store.update_task("t4", "done")
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
            ALL_AGENTS["prd_writer"], msg, self.result, "PRD Writer",
            self.session_service, self.sid, self.user_id,
        )
        self.result.prd = prd
        _write_workspace_file("PRD.md", prd)
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
            ALL_AGENTS["auditor"],
            f"Proof-read this PRD.\n\nRESEARCH BRIEF:\n{brief}\n\nPRD:\n{prd}\n\nReturn your structured verdict.",
            self.result, "Security Auditor",
            self.session_service, self.sid, self.user_id,
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
        content = _read_workspace_file(path)
        if content is None:
            return f"File not found: {path}"
        return content

    async def write_file(self, path: str, content: str) -> str:
        """Write a file to the workspace."""
        return _write_workspace_file(path, content)

    async def save_artifact(self, path: str) -> str:
        """Save a workspace file as an artifact for the user to download."""
        content = _read_workspace_file(path)
        if content is None:
            return f"File not found: {path}"
        # Store the artifact reference in the result for the dashboard
        return f"Artifact '{path}' saved."

    def set_clarify_answer(self, answer: str) -> None:
        """Called by the dashboard when the human answers a clarification."""
        self._clarify_answer = answer
        if self._clarify_answered_event:
            self._clarify_answered_event.set()

    async def clarify(self, question: str) -> str:
        """Ask the human a clarifying question. PAUSES until the human responds.

        Use when the idea is vague, research is contradictory, you need domain
        expertise, or you're presenting results for approval.
        """
        self.result.clarification_question = question
        self.result.clarification_state = "awaiting_response"
        emit("clarify", {
            "question": question,
            "run_id": self.run_id,
        })
        store.log("Orchestrator", "core", f"Clarify: {question[:200]}")

        # Wait for the dashboard to call set_clarify_answer()
        self._clarify_answered_event = asyncio.Event()
        self._clarify_answer = None

        # Yield to allow the SSE event to reach the client
        await asyncio.sleep(0.1)

        # Wait up to 10 minutes for the human to answer
        try:
            await asyncio.wait_for(self._clarify_answered_event.wait(), timeout=600)
        except asyncio.TimeoutError:
            self.result.clarification_state = None
            self.result.clarification_question = None
            return "TIMEOUT: Human did not respond within 10 minutes. Continue with what you have."

        answer = self._clarify_answer or "(no answer)"
        self.result.clarification_state = None
        self.result.clarification_question = None
        store.log("Human", "user", f"Clarify answer: {answer[:200]}")

        # Record the human interaction in the idea tree
        try:
            s = get_store()
            if self.result.idea_id:
                s.note_human_intervention(self.result.idea_id)
        except Exception:
            pass

        return f"HUMAN ANSWER: {answer}"


# ── Verdict + audit parsers ────────────────────────────────────────────

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


# ── Main orchestrator run ──────────────────────────────────────────────

async def run_orchestrator(
    idea: str,
    *,
    inbox: SteeringInbox | None = None,
    session_id: str | None = None,
    resume_idea_id: str | None = None,
) -> OrchestratorResult:
    """Run the autonomous orchestrator loop for one idea.

    The orchestrator is an ADK LlmAgent that calls sub-agent tools. We drive it
    in a managed loop with a quality gate and iteration budget.

    If resume_idea_id is provided, loads previous context (research, debate,
    verdict, PRD) from that idea and injects it into the orchestrator's initial
    state so it can continue from where it left off.

    Returns an OrchestratorResult with the full debate transcript, verdict,
    PRD, and security audit.
    """
    from ..input_guard import guard_input

    inbox = inbox or SteeringInbox()
    result = OrchestratorResult(idea=idea)
    run_id = store.start_run()["run_id"]
    run_manager.manager.start(run_id)

    # Record or resume the idea in the idea tree
    try:
        store_obj = get_store()
        if resume_idea_id:
            # Resume existing idea — load previous context
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
            # New idea
            result.idea_id = store_obj.create_idea(idea[:200])
            store_obj.update_idea_content(result.idea_id, workspace_path=f"runs/{run_id}/")
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
    tools_obj = OrchestratorTools(result, InMemorySessionService(), "", "user", inbox, run_id)
    session_service = InMemorySessionService()
    sid = session_id or (await session_service.create_session(app_name="venturebot", user_id="user")).id
    tools_obj.session_service = session_service
    tools_obj.sid = sid
    _RUNS[run_id] = result

    # Store tools_obj on the result so the dashboard can reach clarify
    result._tools = tools_obj  # type: ignore[attr-defined]

    # Build tools list — ADK function tools
    from google.adk.tools import FunctionTool

    orchestrator_agent = LlmAgent(
        name="orchestrator",
        model=Gemini(model=config.MODEL_ORCHESTRATOR),
        instruction=_ORCHESTRATOR_PROMPT,
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
        description="VentureBot Orchestrator — researches, debates, and produces PRDs autonomously.",
    )

    # Drive loop
    turns_used = 0
    max_turns = config.ORCHESTRATOR_MAX_TURNS
    max_tool_calls = config.ORCHESTRATOR_MAX_TOOL_CALLS
    last_prd: str | None = None
    stall_count = 0

    store.log("System", "core", f"Orchestrator starting: '{idea[:120]}'")
    emit("run_started", {"idea": idea, "run_id": run_id})

    try:
        while turns_used < max_turns:
            run_manager.manager.check()

            # Check quality gate before each turn
            should_stop, reason = _check_quality_gate(result, turns_used, stall_count)
            if should_stop:
                store.log("System", "core", f"Quality gate: {reason}")
                break

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
                    store.log("System", "core", f"Orchestrator complete: PRD ready (avg {avg}) — awaiting human approval")
                else:
                    result.status = "needs_verdict"
                    store.log("System", "core", f"Orchestrator complete: verdict {v} (avg {avg}) — awaiting human decision")
            else:
                result.status = "needs_verdict"
                store.log("System", "core", "Orchestrator complete: incomplete — awaiting human decision")

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
    except Exception as e:
        result.status = "failed"
        result.error = f"{type(e).__name__}: {e}"
        store.set_status("failed")
        store.log("System", "core", f"Orchestrator failed: {result.error}")
        _archive_result(result, run_id)

    _RUNS.pop(run_id, None)
    return result


def _build_turn_prompt(result: OrchestratorResult, turns_used: int, max_turns: int) -> str:
    """Build the prompt the orchestrator sees at the start of each turn."""
    parts = []

    # State summary
    parts.append(f"## Turn {turns_used + 1} of {max_turns}")

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

    if result.clarification_question:
        parts.append(f"\n⚠️  Pending clarification: {result.clarification_question}")

    parts.append("\nTake the NEXT step in the engineering process. If you have a PRD and audit, and they are clean — present to the human. If the PRD needs revision, call write_prd() with specific instructions. If you need information, call research() or clarify().")

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
    """Persist the result to the idea tree."""
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
    except Exception:
        pass


def get_run(run_id: str) -> OrchestratorResult | None:
    """Get a running orchestrator result by run_id (for the dashboard)."""
    return _RUNS.get(run_id)


def answer_clarify(run_id: str, answer: str) -> bool:
    """Answer a pending clarify() call. Returns True if the run was found."""
    result = _RUNS.get(run_id)
    if result is None:
        return False
    tools = getattr(result, "_tools", None)
    if tools is None:
        return False
    tools.set_clarify_answer(answer)
    return True