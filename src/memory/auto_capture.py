"""Fork 1: auto_capture  -- persist session facts after each turn (PRD Sec. 5.2).

Wires into the pipeline's per-agent event stream (the custom orchestrator
does not use ADK's `after_agent_callback` on a root agent, so capture is
driven explicitly from `capture_turn`). Each completed agent message is
saved as a `session_fact` in the memory store, throttled per session.

Throttling: 120s cooldown per session (see _throttle).
"""
from __future__ import annotations

import logging

from ._throttle import try_claim
from .sqlite_store import get_store

logger = logging.getLogger(__name__)


def capture_turn(session_id: str, agent: str, event_type: str, content: str,
                 throttle_state: dict | None = None) -> bool:
    """Persist one completed turn as a session fact.

    Returns True if the fact was saved, False if throttled/skipped.
    """
    if not content or not content.strip():
        return False
    if not try_claim(throttle_state, "auto_capture"):
        return False
    try:
        get_store().save_fact(session_id, agent, event_type, content)
        return True
    except Exception:  # memory is best-effort; never crash the pipeline
        logger.exception("auto_capture: save_fact failed")
        return False


def capture_events(session_id: str, events: list[dict],
                   throttle_state: dict | None = None) -> int:
    """Persist a batch of pipeline events as session facts. Returns count saved."""
    saved = 0
    for ev in events:
        if capture_turn(
            session_id,
            ev.get("agent", "unknown"),
            "agent_message",
            ev.get("text", ""),
            throttle_state,
        ):
            saved += 1
    return saved
