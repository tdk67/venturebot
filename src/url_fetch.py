"""URL ingestion  -- fetch user-provided research URLs safely.

Uses ADK's `load_web_page` (which already has SSRF protection: no redirect
following, and it validates against internal IP ranges). We add our own
scheme + length validation on top and cap how many URLs per checkpoint.
"""
from __future__ import annotations

import re

from google.adk.tools.load_web_page import load_web_page

MAX_URLS_PER_CHECKPOINT = 5
MAX_URL_LENGTH = 2048
MAX_CONTENT_CHARS = 4000

_URL_RE = re.compile(r"^https?://[^\s<>\"']+$", re.IGNORECASE)


def validate_url(url: str) -> bool:
    return bool(_URL_RE.match(url)) and len(url) <= MAX_URL_LENGTH


def fetch_urls(urls: list[str], *, limit: int = MAX_URLS_PER_CHECKPOINT) -> str:
    """Fetch the given URLs and return a combined markdown digest.

    Returns '' if no valid URLs. Truncates each page's text to keep the
    prompt manageable.
    """
    valid = [u for u in urls if validate_url(u)][:limit]
    if not valid:
        return ""

    sections = []
    for url in valid:
        try:
            text = load_web_page(url)
        except Exception as e:  # fail loud per-page, but keep going
            sections.append(f"## {url}\n\n[fetch failed: {type(e).__name__}: {e}]")
            continue
        text = (text or "").strip()
        if len(text) > MAX_CONTENT_CHARS:
            text = text[:MAX_CONTENT_CHARS] + "\n...[truncated]"
        sections.append(f"## {url}\n\n{text}")

    return "\n\n".join(sections)
