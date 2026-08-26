"""Pydantic output schemas for Phase 1 agents (ADK output_schema)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class PriorArt(BaseModel):
    name: str = Field(description="Name of the existing product/project")
    url: str = Field(description="URL to the product/repo")
    gap: str = Field(description="Gap this leaves unfilled")


class Competitor(BaseModel):
    name: str = Field(description="Competitor name")
    url: str = Field(description="Website or product URL")
    pricing: str = Field(default="", description="Pricing model (free, freemium, $X/mo, etc.)")
    strengths: str = Field(default="", description="What they do well")
    weaknesses: str = Field(default="", description="Weaknesses or complaints")
    gap: str = Field(description="Gap this leaves unfilled  -- opportunity for us")


class MarketSignal(BaseModel):
    source: str = Field(description="Where the signal came from (e.g. Reddit, HN, ProductHunt)")
    url: str = Field(description="URL of the source")
    insight: str = Field(description="What the signal implies")


class TechnicalLandscape(BaseModel):
    required_apis: list[str] = Field(default_factory=list)
    libraries: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)


class ResearchBrief(BaseModel):
    idea_summary: str = Field(description="2-3 sentence summary")
    prior_art: list[PriorArt] = Field(default_factory=list, description="Existing products/projects with URLs, gaps identified")
    competitors: list[Competitor] = Field(default_factory=list, description="Competitor landscape with pricing and gaps")
    market_signals: list[MarketSignal] = Field(default_factory=list, description="Community signals from Reddit, HN, ProductHunt, etc.")
    tam_estimate: str = Field(default="", description="Total Addressable Market estimate with source URL")
    sam_estimate: str = Field(default="", description="Serviceable Addressable Market estimate")
    som_estimate: str = Field(default="", description="Serviceable Obtainable Market estimate")
    demand_direction: str = Field(default="", description="rising | falling | stable  -- with evidence")
    funding_activity: list[str] = Field(default_factory=list, description="Recent funding rounds in this space (with URLs if available)")
    review_insights: list[str] = Field(default_factory=list, description="G2/Capterra/user review insights  -- common praise and complaints")
    technical_landscape: TechnicalLandscape = Field(default_factory=TechnicalLandscape, description="Required APIs, libraries, platforms")
    resource_links: list[str] = Field(default_factory=list, description="All key URLs found during research")
    open_questions: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: str | None = None


class Score(BaseModel):
    score: int = Field(description="1-10")
    rationale: str = Field(description="Why this score")


class Scores(BaseModel):
    novelty: Score
    feasibility: Score
    market_fit: Score
    overall_average: float


class ArchitectureDecision(BaseModel):
    topic: str
    decision: str
    advocate_position: str
    critic_position: str
    chosen_approach: str
    rationale: str


class JudgeVerdict(BaseModel):
    scores: Scores
    verdict: str = Field(description="PROCEED | PARK | PRUNE")
    verdict_rationale: str
    key_risks: list[str] = Field(default_factory=list)
    architecture_decisions: list[ArchitectureDecision] = Field(default_factory=list)


class AuditFinding(BaseModel):
    section: str
    line: str | None = None
    issue: str
    severity: str = Field(description="critical | high | medium | low")


class SecurityAudit(BaseModel):
    verdict: str = Field(description="PASS | FLAG")
    findings: list[AuditFinding] = Field(default_factory=list)
