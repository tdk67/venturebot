"""Idea tree pruning rules — PRD §5.5 (deterministic, enforced by dream_review).

Pure functions over idea-tree rows; no LLM, no I/O. This keeps the pruning
policy auditable and unit-testable. `dream_review` calls `prune_ideas` and
writes the resulting status changes back through the MemoryStore.

Pruning rules (from PRD §5.5):
  - score < 5 with 0 human interventions           → PRUNE after 24h
  - score < 5 with ≥1 human intervention           → PARK (human wants it)
  - no activity in 7 days                          → PARK
  - PARKED for 30 days                             → PRUNE
  - human explicitly REJECTED                      → PRUNE (keep record)
  - human explicitly APPROVED + Phase 2 succeeded  → keep ACTIVE
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

_SCORE_KEYS = ("novelty", "feasibility", "market_fit")

# Time windows (seconds) — module constants so tests can reason about them.
PRUNE_AFTER_SECONDS = 24 * 3600          # score < 5, no human touch
PARK_INACTIVITY_SECONDS = 7 * 24 * 3600  # no activity in 7 days
PRUNE_PARKED_SECONDS = 30 * 24 * 3600    # PARKED for 30 days


@dataclass
class PruneDecision:
    """A single idea's pruning outcome."""
    idea_id: str
    action: str              # "KEEP" | "PARK" | "PRUNE"
    reason: str
    score: float | None = None


@dataclass
class PruneResult:
    decisions: list[PruneDecision] = field(default_factory=list)

    def changes(self) -> list[dict]:
        """Status changes only (KEEP is a no-op)."""
        return [
            {"idea_id": d.idea_id, "status": d.action, "reason": d.reason}
            for d in self.decisions if d.action != "KEEP"
        ]


def _overall_average(scores_json: str | None) -> float | None:
    """Average of novelty/feasibility/market_fit; None if unscoreable."""
    if not scores_json:
        return None
    try:
        scores = json.loads(scores_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(scores, dict):
        return None
    nums = []
    for key in _SCORE_KEYS:
        s = scores.get(key)
        if isinstance(s, dict) and isinstance(s.get("score"), (int, float)):
            nums.append(float(s["score"]))
        elif isinstance(s, (int, float)):
            nums.append(float(s))
    return sum(nums) / len(nums) if nums else None


def _age(ts: float | None, now: float) -> float | None:
    return (now - ts) if ts else None


def decide_idea(idea: dict, *, now: float | None = None) -> PruneDecision:
    """Apply pruning rules to one idea row. Returns the recommended action."""
    now = now or time.time()
    idea_id = idea["id"]
    status = (idea.get("status") or "ACTIVE").upper()
    score = _overall_average(idea.get("scores"))
    interventions = int(idea.get("human_intervention_count") or 0)
    updated_at = idea.get("updated_at")

    # PRUNED is terminal (unless a later decision revives it — not our job here).
    if status == "PRUNED":
        return PruneDecision(idea_id, "PRUNE", "already pruned", score)

    inactivity = _age(updated_at, now)

    if status == "PARK":
        if inactivity is not None and inactivity >= PRUNE_PARKED_SECONDS:
            return PruneDecision(idea_id, "PRUNE", "parked for 30 days", score)
        return PruneDecision(idea_id, "KEEP", "still within park window", score)

    # status == ACTIVE
    if score is not None and score < 5:
        if interventions == 0:
            if inactivity is not None and inactivity >= PRUNE_AFTER_SECONDS:
                return PruneDecision(idea_id, "PRUNE",
                                     f"score {score:.1f} < 5, no human intervention, idle >24h", score)
            return PruneDecision(idea_id, "KEEP", "low score but within 24h grace", score)
        # human cared about it → park, don't prune
        return PruneDecision(idea_id, "PARK", "score < 5 but human intervened", score)

    # score >= 5 (or unknown)
    if inactivity is not None and inactivity >= PARK_INACTIVITY_SECONDS:
        return PruneDecision(idea_id, "PARK", "no activity in 7 days", score)

    return PruneDecision(idea_id, "KEEP", "active and healthy", score)


def prune_ideas(ideas: list[dict], *, now: float | None = None) -> PruneResult:
    """Run pruning rules over a list of idea rows and collect decisions."""
    return PruneResult(decisions=[decide_idea(i, now=now) for i in ideas])
