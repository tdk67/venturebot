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

# The Creative head runs hot  -- a dedicated higher-temperature GenerateContentConfig.
_CREATIVE_GCC = types.GenerateContentConfig(temperature=config.CREATIVE_TEMPERATURE)

# Factory function to create agents with optional custom API key (for BYOK)
def create_agents(api_key: str | None = None) -> dict[str, LlmAgent]:
    """Create all Phase 1 agents with an optional custom API key.
    
    If api_key is provided, all agents will use it instead of the default
    GOOGLE_API_KEY from environment. This enables Bring Your Own Key (BYOK).
    """
    model_kwargs = {'client_kwargs': {'api_key': api_key}} if api_key else {}
    
    researcher = LlmAgent(
        name="researcher",
        model=Gemini(model=config.MODEL_RESEARCHER, **model_kwargs),
        instruction=prompts.RESEARCHER_PROMPT,
        tools=[google_search, LongRunningFunctionTool(clarify_question)],
        generate_content_config=_SEARCH_GCC,
        output_schema=schemas.ResearchBrief,
        output_key="research_brief",
        description="Researches a vague idea into a structured brief (web search + clarification).",
    )
    
    advocate = LlmAgent(
        name="advocate",
        model=Gemini(model=config.MODEL_ADVOCATE, **model_kwargs),
        instruction=prompts.ADVOCATE_PROMPT,
        tools=[],
        output_key="advocate_argument",
        description="Argues FOR the idea, using only the research brief (no web search).",
    )
    
    critic = LlmAgent(
        name="critic",
        model=Gemini(model=config.MODEL_CRITIC, **model_kwargs),
        instruction=prompts.CRITIC_PROMPT,
        tools=[google_search],
        generate_content_config=_SEARCH_GCC,
        output_key="critic_rebuttal",
        description="Red-team challenges every Advocate claim, with web-sourced counter-evidence.",
    )
    
    judge = LlmAgent(
        name="judge",
        model=Gemini(model=config.MODEL_JUDGE, **model_kwargs),
        instruction=prompts.JUDGE_PROMPT,
        tools=[],
        output_schema=schemas.JudgeVerdict,
        output_key="verdict",
        description="Weighs both sides and produces a structured PROCEED/PARK/PRUNE verdict.",
    )
    
    prd_writer = LlmAgent(
        name="prd_writer",
        model=Gemini(model=config.MODEL_PRD_WRITER, **model_kwargs),
        instruction=prompts.PRD_WRITER_PROMPT,
        tools=[],
        output_key="prd",
        description="Writes a detailed, implementable PRD from research + debate + verdict.",
    )
    
    auditor = LlmAgent(
        name="auditor",
        model=Gemini(model=config.MODEL_AUDITOR, **model_kwargs),
        instruction=prompts.AUDITOR_PROMPT,
        tools=[],
        output_schema=schemas.SecurityAudit,
        output_key="security_audit",
        description="Security auditor that proof-reads the PRD for hallucinations and gaps.",
    )
    
    creative = LlmAgent(
        name="creative",
        model=Gemini(model=config.MODEL_CREATIVE, **model_kwargs),
        instruction=prompts.CREATIVE_PROMPT,
        tools=[],
        generate_content_config=_CREATIVE_GCC,
        output_key="creative_angles",
        description="Generates creative pivots, niches, and unfair advantages.",
    )
    
    return {
        "researcher": researcher,
        "advocate": advocate,
        "critic": critic,
        "judge": judge,
        "prd_writer": prd_writer,
        "auditor": auditor,
        "creative": creative,
    }


# Default agents (using GOOGLE_API_KEY from environment)
ALL_AGENTS = create_agents()
