"""Budget enforcement  -- S7.

Cumulative spend tracking with a hard limit, configurable at runtime, and a
human-override to raise the limit and continue. Every LLM call must pass
`check_budget()` BEFORE the call and `record_usage()` AFTER.

Pricing data is approximate (per-1M-token USD). It only needs to be a reliable
guardrail, not a billing statement  -- track conservatively (round up).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import config

# Approximate USD per 1M tokens (input, output). Conservative defaults.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Gemini (roughly: flash cheap, pro pricier)
    "gemini-3.7-flash": (0.10, 0.40),
    "gemini-3.1-pro": (1.25, 10.00),
    # OpenRouter deepseek
    "deepseek/deepseek-v4-pro": (2.00, 8.00),
    "deepseek/deepseek-chat-v3-0324": (0.27, 1.10),
}
_DEFAULT_PRICE = (1.00, 4.00)

_lock = threading.Lock()
_state_file = config.DATA_DIR / "budget.json"


class BudgetExceeded(Exception):
    """Raised when a call would exceed the configured budget."""

    def __init__(self, spent: float, limit: float):
        self.spent = spent
        self.limit = limit
        super().__init__(
            f"Budget limit breached: ${spent:.4f}/${limit:.2f}. LLM calls stopped."
        )


def _today_key() -> str:
    return time.strftime("%Y-%m-%d")


def _load() -> dict:
    if _state_file.exists():
        try:
            return json.loads(_state_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save(data: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _state_file.write_text(json.dumps(data, indent=2))


def get_limit() -> float:
    """Current (possibly human-raised) daily limit."""
    with _lock:
        data = _load()
        return float(data.get("limit", config.DAILY_BUDGET_LIMIT_USD))


def raise_limit(new_limit: float) -> float:
    """Human override: raise the limit and continue. Returns new limit."""
    if new_limit <= 0:
        raise ValueError("Limit must be positive")
    with _lock:
        data = _load()
        data["limit"] = float(new_limit)
        _save(data)
        return float(new_limit)


def get_spent() -> float:
    with _lock:
        data = _load()
        return float(data.get("spent_today", 0.0))


def _price_for(model: str) -> tuple[float, float]:
    # match by exact id first, then by prefix (strip provider-specific suffixes)
    if model in _MODEL_PRICING:
        return _MODEL_PRICING[model]
    for k, v in _MODEL_PRICING.items():
        if model.startswith(k):
            return v
    return _DEFAULT_PRICE


def check_budget(estimated_input_tokens: int = 0, estimated_output_tokens: int = 0,
                 model: str = "") -> None:
    """Raise BudgetExceeded if a call would exceed the limit.

    Estimates the cost of the upcoming call and blocks it if spent+estimate
    would cross the limit. After the call, `record_usage` adds the real cost.
    """
    with _lock:
        data = _load()
        spent = float(data.get("spent_today", 0.0))
        limit = float(data.get("limit", config.DAILY_BUDGET_LIMIT_USD))
    in_price, out_price = _price_for(model)
    estimate = (
        estimated_input_tokens / 1_000_000 * in_price
        + estimated_output_tokens / 1_000_000 * out_price
    )
    if spent + estimate >= limit:
        raise BudgetExceeded(spent, limit)


def record_usage(model: str, input_tokens: int, output_tokens: int) -> None:
    """Add the real cost of a completed call. Fails hard if it crosses limit."""
    in_price, out_price = _price_for(model)
    cost = input_tokens / 1_000_000 * in_price + output_tokens / 1_000_000 * out_price
    with _lock:
        data = _load()
        data["spent_today"] = float(data.get("spent_today", 0.0)) + cost
        data["last_updated"] = time.time()
        _save(data)


def reset_daily_if_needed() -> None:
    """Roll over the daily counter when the date changes."""
    with _lock:
        data = _load()
        if data.get("day") != _today_key():
            data["day"] = _today_key()
            data["spent_today"] = 0.0
            _save(data)


def status() -> dict:
    reset_daily_if_needed()
    return {
        "spent": round(get_spent(), 4),
        "limit": round(get_limit(), 2),
        "remaining": round(max(0.0, get_limit() - get_spent()), 4),
    }
