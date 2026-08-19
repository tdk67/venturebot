"""Per-session throttling for fork-style callbacks (adapted from Long Horizon).

`auto_capture` and `review_fork` fire after every agent turn; on bursts of
short turns that's wasteful (the second run works against a near-identical
snapshot while the first is still in flight). `try_claim` enforces:

  - a cooldown window (default 120s) between runs of the same fork type, and
  - a per-session cap (default 50) as a safety valve against runaway loops.

Set VENTUREBOT_FORK_COOLDOWN=0 to disable the cooldown (the cap still applies).
"""
from __future__ import annotations

import os
import time
from typing import Any

_STATE_KEY = "_fork_throttle"
_COOLDOWN_SECONDS = 120.0
_PER_SESSION_CAP = 50
_COOLDOWN_ENV = "VENTUREBOT_FORK_COOLDOWN"


def _cooldown_seconds() -> float:
    raw = os.environ.get(_COOLDOWN_ENV)
    if raw is None:
        return _COOLDOWN_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _COOLDOWN_SECONDS


def try_claim(state: Any, fork_type: str) -> bool:
    """Try to claim a fork slot for ``fork_type`` on this turn.

    Returns True (and records the run in ``state``) when the cooldown has
    elapsed and the per-session cap is not yet hit. Returns False otherwise.
    """
    if state is None:
        return True

    now = time.time()
    raw = state.get(_STATE_KEY)
    throttle: dict[str, Any] = raw if isinstance(raw, dict) else {}

    entry = throttle.get(fork_type)
    if not isinstance(entry, dict):
        entry = {}
    try:
        count = int(entry.get("count", 0) or 0)
        last_at = float(entry.get("last_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        count = 0
        last_at = 0.0

    if count >= _PER_SESSION_CAP:
        return False

    cooldown = _cooldown_seconds()
    if cooldown > 0 and last_at > 0 and (now - last_at) < cooldown:
        return False

    throttle[fork_type] = {"count": count + 1, "last_at": now}
    state[_STATE_KEY] = throttle
    return True
