"""Gemini (AI Studio prepaid) usage tracker + credit check  -- local spend ledger.

AI Studio has NO public API for the prepaid credit balance. What we CAN do:
  1. Track every Gemini call's real token usage (usage_metadata) locally, in $.
  2. Estimate remaining credits = topup_amount - tracked_spend.
  3. Do a live 1-token smoke call to detect "credits depleted" (429).

This gives an early-warning signal before you hit zero, and a one-command
health check. It cannot read the exact $ on the AI Studio billing page.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import config

LEDGER_FILE = config.DATA_DIR / "gemini_usage.json"

# $ per 1M tokens (input, output). Gemini 3.x flash is cheap; pro is pricier.
PRICING = {
    "gemini-3.7-flash": (0.10, 0.40),
    "gemini-3.6-flash": (0.10, 0.40),
    "gemini-3.5-flash": (0.10, 0.40),
    "gemini-3.1-pro-preview": (1.25, 10.00),
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.5-pro": (1.25, 10.00),
}
DEFAULT_PRICE = (0.50, 2.00)


def _price(model: str) -> tuple[float, float]:
    for k, v in PRICING.items():
        if model.startswith(k):
            return v
    return DEFAULT_PRICE


def _load() -> dict:
    if LEDGER_FILE.exists():
        try:
            return json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"calls": [], "total_input_tokens": 0, "total_output_tokens": 0,
            "total_cost": 0.0, "topup_amount": 0.0}


def _save(data: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    LEDGER_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def record(model: str, input_tokens: int, output_tokens: int) -> float:
    """Record one Gemini call's usage. Returns its cost in $."""
    in_price, out_price = _price(model)
    cost = input_tokens / 1e6 * in_price + output_tokens / 1e6 * out_price
    data = _load()
    data["calls"].append({
        "ts": time.time(),
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost": round(cost, 6),
    })
    data["total_input_tokens"] += input_tokens
    data["total_output_tokens"] += output_tokens
    data["total_cost"] = round(data["total_cost"] + cost, 6)
    _save(data)
    return cost


def set_topup(amount_usd: float) -> None:
    """Set the last-known topup amount so we can estimate remaining."""
    data = _load()
    data["topup_amount"] = round(float(amount_usd), 2)
    _save(data)


def remaining_estimate() -> float | None:
    """Remaining = topup - spent. None if topup unknown."""
    data = _load()
    if data["topup_amount"] <= 0:
        return None
    return round(max(0.0, data["topup_amount"] - data["total_cost"]), 4)


def summary() -> dict:
    data = _load()
    return {
        "calls": len(data["calls"]),
        "total_input_tokens": data["total_input_tokens"],
        "total_output_tokens": data["total_output_tokens"],
        "total_cost": round(data["total_cost"], 4),
        "topup_amount": data["topup_amount"],
        "remaining_estimate": remaining_estimate(),
    }


def live_smoke_check() -> tuple[bool, str]:
    """Do a minimal live call to detect 'credits depleted' (429)."""
    from google import genai
    try:
        client = genai.Client(api_key=config.google_api_key())
        # Use Chat.send_message (recommended over generate_content for AFC).
        chat = client.chats.create(model=config.MODEL_RESEARCHER)
        r = chat.send_message("Reply with exactly: OK")
        usage = getattr(r, "usage_metadata", None)
        if usage is not None:
            record(
                config.MODEL_RESEARCHER,
                getattr(usage, "prompt_token_count", 0) or 0,
                getattr(usage, "candidates_token_count", 0) or 0,
            )
        return True, "credits available (live call OK)"
    except Exception as e:
        msg = str(e)
        if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "depleted" in msg.lower():
            return False, "CREDITS DEPLETED  -- top up at https://aistudio.google.com/billing"
        return False, f"check failed: {msg[:200]}"
