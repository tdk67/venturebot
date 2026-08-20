"""Fork 3: dream_review — nightly consolidation + idea-tree pruning (PRD §5.4).

Algorithm:
  1. Load recent session facts (last 24h).
  2. Run the deterministic idea-tree pruning rules (§5.5).
  3. Optionally call the LLM to consolidate lessons / update profile.
  4. Write all changes back through the MemoryStore.

The LLM consolidation is injected as a callable so the deterministic core
(pruning, collection) is unit-testable without a live model. The endpoint
(POST /scheduler/dream-review) lives in dashboard.py and calls
`run_dream_review`.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Callable

from .idea_tree import prune_ideas
from .sqlite_store import MemoryStore

logger = logging.getLogger(__name__)

_CONSOLIDATE_PROMPT = """You are VentureBot's nightly self-improvement engine.

Review the following sessions from today:
{transcripts}

Current state:
- Profile: {profile}
- Lessons: {lessons}
- Techniques: {techniques}
- Idea Tree: {idea_tree}

TASKS:
1. CONSOLIDATE LESSONS: Merge similar lessons, resolve contradictions.
   Delete lessons that were one-off mistakes, keep recurring patterns.
2. UPDATE PROFILE: What did you learn about the user? Preferences, style,
   recurring decisions, technical preferences?
3. CURATE TECHNIQUES: Promote techniques that led to approvals, retire
   techniques associated with repeated failures.

Output STRICT JSON:
{{"consolidated_lessons": [{{"name": "...", "rule": "..."}}],
  "profile_updates": {{"key": "value"}},
  "promoted_techniques": ["name"],
  "retired_techniques": ["name"]}}
"""


def collect_recent_facts(store: MemoryStore, since_seconds: float = 24 * 3600,
                         limit: int = 500) -> list[dict]:
    """Load session facts from the last ``since_seconds`` seconds."""
    return store.get_facts(since=time.time() - since_seconds, limit=limit)


def build_consolidate_prompt(store: MemoryStore, facts: list[dict]) -> str:
    transcripts = "\n\n".join(
        f"[{f['agent']}] {f['content']}" for f in facts
    ) or "(no activity today)"
    return _CONSOLIDATE_PROMPT.format(
        transcripts=transcripts,
        profile=json.dumps(store.get_profile()),
        lessons=json.dumps(store.get_lessons(active_only=True, limit=50)),
        techniques=json.dumps(store.get_techniques(active_only=True)),
        idea_tree=json.dumps(store.get_idea_tree()),
    )


def _default_consolidate_call(prompt: str) -> dict | None:
    """Live Gemini consolidation via ADK."""
    from .review_fork import _default_llm_call, _extract_json
    raw = _default_llm_call(prompt)
    return _extract_json(raw)


def _apply_consolidation(store: MemoryStore, data: dict) -> dict:
    """Write consolidation results back. Returns a change summary."""
    summary = {"lessons": 0, "profile_keys": 0, "promoted": [], "retired": []}

    for lesson in data.get("consolidated_lessons", []) or []:
        if isinstance(lesson, dict) and lesson.get("name"):
            store.save_lesson(lesson["name"], lesson.get("rule", ""), "dream_review")
            summary["lessons"] += 1

    profile = data.get("profile_updates")
    if isinstance(profile, dict) and profile:
        store.update_profile({k: str(v) for k, v in profile.items()})
        summary["profile_keys"] = len(profile)

    for name in data.get("promoted_techniques", []) or []:
        # "promote" = un-retire + bump success count
        store.save_technique(name, "", "")
        store.record_technique_outcome(name, success=True)
        summary["promoted"].append(name)

    for name in data.get("retired_techniques", []) or []:
        if store.retire_technique(name):
            summary["retired"].append(name)

    return summary


def run_dream_review(store: MemoryStore | None = None,
                     consolidate_call: Callable[[str], dict | None] | None = None,
                     since_seconds: float = 24 * 3600) -> dict:
    """Run the nightly consolidation. Returns a summary dict.

    ``consolidate_call`` is optional: when omitted (or when it returns None),
    the deterministic pruning still runs and the summary reflects that.
    """
    store = store or MemoryStore()
    summary = {
        "facts_collected": 0,
        "idea_changes": [],
        "consolidation": None,
    }

    # 1. Collect recent facts.
    facts = collect_recent_facts(store, since_seconds)
    summary["facts_collected"] = len(facts)

    # 2. Deterministic idea-tree pruning.
    ideas = store.get_idea_tree()
    result = prune_ideas(ideas)
    for change in result.changes():
        store.update_idea_status(change["idea_id"], change["status"], change["reason"])
        summary["idea_changes"].append(change)

    # 3. LLM consolidation (best-effort; never crash the job).
    call = consolidate_call if consolidate_call is not None else _default_consolidate_call
    try:
        prompt = build_consolidate_prompt(store, facts)
        data = call(prompt)
        if data:
            summary["consolidation"] = _apply_consolidation(store, data)
    except Exception:
        logger.exception("dream_review: consolidation failed")

    return summary
