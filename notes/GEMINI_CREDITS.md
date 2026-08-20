# Gemini (AI Studio) Credits — How to Check, Top Up, and Track

**You keep forgetting this, so here's the durable version.**

## TL;DR — Top up when credits are low/depleted

1. Go to **https://aistudio.google.com/billing**
2. On that page there is a **"Payments"** box.
3. Inside/next to it is a **"Buy credits"** button.
4. ⚠️ **That button is a faint/faded color** — your eye skips it (this is the
   exact thing that wasted your time before). It IS the button you want.
5. Click it, pick an amount, pay with your saved card, done.

## How to CHECK credits (three ways)

### 1. One command (automated, no UI)
```bash
cd /root/venturebot
./scripts/check_gemini_credits.sh
```
This does a **live smoke call** (fails with a clear message if credits are
depleted → 429 RESOURCE_EXHAUSTED) and prints your **tracked local spend** +
estimated remaining.

### 2. The billing page (the ONLY source of the exact $ balance)
https://aistudio.google.com/billing
- There is **no public API** for the prepaid balance — Google doesn't expose it.
- So our automated check estimates from local usage tracking + a live call.
  It tells you *"still working"* vs *"depleted, top up"*, but not the exact $.

### 3. Track your own topup amount (so remaining is accurate)
```bash
cd /root/venturebot
./venv/bin/python -c "from venturebot.gemini_usage import set_topup; set_topup(10.00)"
```
Set this to whatever you just bought. Then `check_gemini_credits.sh` will show
`remaining = topup - tracked_spend`.

## Fact-check (2026-08-19): is there really no API?

A suggested "use Cloud Billing `billingAccounts.get`" answer was verified
against the official reference — it is **partially hallucinated**:

- `billingAccounts.get` **exists** (GET, OAuth2, `roles/billing.viewer` is the
  correct read-only IAM role).
- BUT its `BillingAccount` response has only 6 fields — `name`, `open`,
  `displayName`, `masterBillingAccount`, `parent`, `currencyCode` — **none of
  which are spend/credit/balance**. So it CANNOT read your credit balance.
- AI Studio **prepaid credits** (the `429 prepayment credits depleted` thing)
  are a DIFFERENT system from Google Cloud Billing (which is postpaid /
  "Paid" tier). AI Studio prepaid balance has **no documented public API**.
- Real Cloud Billing *spend* data lives in **Billing data exports → BigQuery**
  (or the Billing Budget API), not in `getBillingAccount` — and still won't
  show the AI Studio prepaid number.

Conclusion: local usage ledger + live 429-detection + the
`aistudio.google.com/billing` page is the correct approach.

## Why this is annoying (so you stop blaming yourself)

- Google **renames/redesigns** the AI Studio + GCP panels constantly.
- **Google Search gives wrong advice** because it's trained on older UIs.
- The **"Buy credits" button is intentionally subtle** (faint color) — a UX
  dark pattern, not your eyesight.
- There's **no API** for credit balance, only for per-call token usage.

## Automatic reminder

Ask me to schedule a recurring check (e.g. daily) — I'll run
`check_gemini_credits.sh` and only ping you when it fails or spend is high.

## Files

- `venturebot/gemini_usage.py` — ledger + live check
- `scripts/check_gemini_credits.sh` — one-command health check
