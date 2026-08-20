"""Phase 1 agents: Researcher, Advocate, Critic, Judge, PRD Writer.

Built on ADK 2.7.1. Each agent is independently instantiable and testable;
the orchestrator (pipeline.py) wires them together with the kill switch and
HITL gates.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.tools import LongRunningFunctionTool, google_search
from google.genai import types

from .. import config
from . import prompts, schemas
from .clarify import clarify_question

# Built-in tools (google_search) require server-side tool invocations enabled
# when used with function calling (ADK 2.7 requirement).
_SEARCH_GCC = types.GenerateContentConfig(
    tool_config=types.ToolConfig(include_server_side_tool_invocations=True)
)

# The Creative head runs hot — a dedicated higher-temperature GenerateContentConfig.
_CREATIVE_GCC = types.GenerateContentConfig(temperature=config.CREATIVE_TEMPERATURE)

# ── Researcher (has google_search + clarify HITL) ─────────────────────
researcher_agent = LlmAgent(
    name="researcher",
    model=Gemini(model=config.MODEL_RESEARCHER),
    instruction=prompts.RESEARCHER_PROMPT,
    tools=[google_search, LongRunningFunctionTool(clarify_question)],
    generate_content_config=_SEARCH_GCC,
    output_schema=schemas.ResearchBrief,
    output_key="research_brief",
    description="Researches a vague idea into a structured brief (web search + clarification).",
)

# ── Advocate (NO tools — blind separation from Critic) ────────────────
advocate_agent = LlmAgent(
    name="advocate",
    model=Gemini(model=config.MODEL_ADVOCATE),
    instruction=prompts.ADVOCATE_PROMPT,
    tools=[],  # intentionally empty — blind debate
    output_key="advocate_argument",
    description="Argues FOR the idea, using only the research brief (no web search).",
)

# ── Critic (HAS google_search — the asymmetry) ────────────────────────
critic_agent = LlmAgent(
    name="critic",
    model=Gemini(model=config.MODEL_CRITIC),
    instruction=prompts.CRITIC_PROMPT,
    tools=[google_search],
    generate_content_config=_SEARCH_GCC,
    output_key="critic_rebuttal",
    description="Red-team challenges every Advocate claim, with web-sourced counter-evidence.",
)

# ── Judge (structured verdict) ────────────────────────────────────────
judge_agent = LlmAgent(
    name="judge",
    model=Gemini(model=config.MODEL_JUDGE),
    instruction=prompts.JUDGE_PROMPT,
    tools=[],
    output_schema=schemas.JudgeVerdict,
    output_key="verdict",
    description="Weighs both sides and produces a structured PROCEED/PARK/PRUNE verdict.",
)

# ── PRD Writer (synthesizes everything into a PRD) ────────────────────
prd_writer_agent = LlmAgent(
    name="prd_writer",
    model=Gemini(model=config.MODEL_PRD_WRITER),
    instruction=prompts.PRD_WRITER_PROMPT,
    tools=[],
    output_key="prd",
    description="Writes a detailed, implementable PRD from research + debate + verdict.",
)

# ── Security Auditor (S10 — proof-reads the PRD before approval) ──────
auditor_agent = LlmAgent(
    name="auditor",
    model=Gemini(model=config.MODEL_AUDITOR),
    instruction=prompts.AUDITOR_PROMPT,
    tools=[],
    output_schema=schemas.SecurityAudit,
    output_key="security_audit",
    description="Proof-reads the PRD for hallucinated claims, injection residue, and missing NFRs.",
)

# ── Creative Ideator (divergent head — higher temperature) ─────────────
# Blind like the Advocate (no search): it imagines, the Critic verifies.
creative_agent = LlmAgent(
    name="creative",
    model=Gemini(model=config.MODEL_CREATIVE),
    instruction=prompts.CREATIVE_PROMPT,
    tools=[],
    generate_content_config=_CREATIVE_GCC,
    output_key="creative_angles",
    description="Divergent thinker: hunts niches, pivots, unfair advantages and wild ideas.",
)

ALL_AGENTS = {
    "researcher": researcher_agent,
    "advocate": advocate_agent,
    "critic": critic_agent,
    "judge": judge_agent,
    "prd_writer": prd_writer_agent,
    "auditor": auditor_agent,
    "creative": creative_agent,
}
