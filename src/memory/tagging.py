"""Lightweight keyword-based tag extraction (IDEA_HISTORY_ADDENDUM §tags).

Tags are derived from idea text (title + research brief + debate transcript),
not user-entered, so the idea timeline can be filtered by category without
any extra UI to maintain. The rules are deliberately simple and auditable —
this is a heuristic, not an ontology.
"""
from __future__ import annotations

TAG_RULES: dict[str, list[str]] = {
    "frontend": ["ui", "dashboard", "frontend", "react", "vue", "spa", "web app"],
    "backend": ["api", "backend", "database", "rest", "graphql", "microservice"],
    "fullstack": ["fullstack", "full-stack"],
    "ai": ["ai", "ml", "llm", "gpt", "gemini", "claude", "model", "training"],
    "cli": ["cli", "command line", "terminal", "tui"],
    "tool": ["tool", "utility", "generator", "converter"],
    "mobile": ["ios", "android", "mobile", "app"],
    "devtools": ["git", "debug", "devtools", "ide", "plugin"],
    "infra": ["deploy", "docker", "kubernetes", "ci/cd", "infra"],
    "data": ["data", "etl", "pipeline", "analytics", "visualization"],
}


def extract_tags(*texts: str | None) -> list[str]:
    """Return the tags whose keywords appear anywhere in the given texts.

    Case-insensitive substring match. Order follows TAG_RULES insertion order.
    """
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return []
    return [tag for tag, keywords in TAG_RULES.items() if any(k in blob for k in keywords)]
