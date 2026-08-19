#!/usr/bin/env bash
# check_gemini_credits — one-command Gemini credit health check.
#  - live smoke call (proves credits aren't depleted / 429)
#  - local tracked usage + estimated remaining (from your last topup)
# Usage: ./scripts/check_gemini_credits.sh
set -u
cd "$(dirname "$0")/.."
./venv/bin/python - <<'PY'
import sys; sys.path.insert(0, ".")
from venturebot import gemini_usage, config

print("=" * 60)
print("Gemini (AI Studio prepaid) credit check")
print("=" * 60)
print(f"Model for smoke test: {config.MODEL_RESEARCHER}")
ok, msg = gemini_usage.live_smoke_check()
print(f"Live check : {msg}")

s = gemini_usage.summary()
print(f"Calls tracked: {s['calls']}")
print(f"Tokens used : {s['total_input_tokens']:,} in / {s['total_output_tokens']:,} out")
print(f"Spend tracked: ${s['total_cost']:.4f}")
if s["remaining_estimate"] is not None:
    print(f"Est. remaining: ${s['remaining_estimate']:.2f}  (topup ${s['topup_amount']:.2f} - tracked spend)")
else:
    print("Est. remaining: UNKNOWN (set last topup with: set_topup(amount))")

if not ok:
    print()
    print("=" * 60)
    print("ACTION: Top up credits.")
    print("  URL: https://aistudio.google.com/billing")
    print("  Look for the 'Payments' box and the 'Buy credits' button")
    print("  (it is a FADER-colored button — easy to miss!).")
    print("=" * 60)
PY
