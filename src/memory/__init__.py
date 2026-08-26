"""Self-improvement memory layer (M3).

Three-fork pattern (PRD Sec. 5):
  - auto_capture    -- persist session facts after each turn (throttled)
  - review_fork     -- fire-and-forget LLM analysis of the last turn
  - dream_review    -- nightly consolidation + idea-tree pruning

Backed by a SQLite store (stage 1 / VPS). The same API is designed to be
swappable for Vertex AI Memory Bank in stage 3 (GCP).
"""
from __future__ import annotations

from .sqlite_store import MemoryStore, get_store
from .idea_tree import decide_idea, prune_ideas

__all__ = ["MemoryStore", "get_store", "decide_idea", "prune_ideas"]
