# VentureBot — UI/UX Improvement Notes (deferred — note, don't fix yet)

> **Recorded:** 2026-08-19 during live testing by Tamas.
> **Rule:** note down first, fix later. Don't touch code yet. These will become proper tasks when we rework the UI against a stub.

---

## 1. Debate Start Has No Feedback

**Problem:** Pressing "Start debate" gives absolutely no visual acknowledgement. The user pressed it multiple times thinking nothing happened.

**Fix needed:**
- Button → immediately disabled + spinner + "Running..." text.
- Client-side debounce: lock out further clicks for 500ms after first.
- Backend idempotency guard: if a debate is already running for this idea, return the existing run_id (don't spawn a duplicate run).

---

## 2. No Turn/Progress Indicator During Debate

**Problem:** There is no visual indication of:
- Which agent is currently active (Researcher? Advocate? Critic? Judge? PRD Writer?)
- What step/phase the pipeline is in
- How many turns have elapsed
- Whether it's still running or has stalled

**Fix needed:**
- A "current agent" badge/label in the UI (e.g., `🔍 Researcher is searching the web...`).
- A phase progress bar: `Research → Advocate → Critic → Judge → PRD`.
- Turn counter: "Turn 2 of 3 — Critic rebutting".
- Elapsed time: "Running for 1:23".
- All driven by SSE events that the backend already has (but the frontend doesn't render).

---

## 3. Missing "Rebut" Option at Verdict Gate

**Problem:** When the Judge returns PARK or PRUNE (found real competitors but couldn't cite URLs), the human currently has only two options:

| Option | What it does |
|---|---|
| `proceed` | Force PRD Writer anyway (what you used) |
| `abort` / `reject` | Kill the run |

**Missing third option: `rebut`** — "You missed something, re-evaluate with this feedback."

**Fix needed:** Add a `"rebut"` decision in `/api/resume` that:
1. Injects the human's steering text as new evidence into the inbox.
2. Re-enters the debate loop at Advocate (with the fresh steering), NOT just proceeds to PRD.
3. Advocate → Critic → Judge re-run with the new evidence.
4. Flow: `Judge PRUNE → Human says "you missed Competitor X at url.com" → Advocate re-argues → Critic re-challenges → Judge re-scores`.

---

## 4. Missing Duplicate / Similar Idea Check

**Problem:** No check prevents submitting the same idea twice, or an idea very similar to an existing one.

**Fix needed:**
- On idea submission (`POST /api/ideas` / debate start): do a similarity check against existing ideas.
- Options:
  - **Simple:** SQLite `LIKE` / FTS on idea titles (fast, no LLM cost).
  - **Better:** Cheap embedding comparison (all-MiniLM-L6-v2) against existing ideas' titles + research briefs.
- If a duplicate/similar is found: show a warning modal: "You submitted a similar idea on [date]. Resume that run instead?" with `[Resume existing] [Submit anyway]` buttons.

---

## 5. Missing "Delete Idea" Functionality

**Problem:** No way to remove an idea from the idea tree that you don't want to keep. Currently only `archive` exists (marks PARK), not delete.

**Fix needed:**
- `DELETE /api/ideas/{idea_id}` — actually removes the row from SQLite (`idea_tree`).
- Confirmation dialog: "Delete this idea and all its research/debate/PRD data? This cannot be undone."
- Only deletable when not actively running.
- Soft-delete alternative: `status = 'DELETED'` + filtered out of UI (recoverable).

---

## 6. Usage & Cost Visibility (const should be more visible)

**Problem:** Cost/budget info exists (`/api/state` → `budget`) but is barely visible — a tiny badge showing `budget: $0.00/$20.00` in the header. No breakdown of what's actually being spent.

**Fix needed:**

### 6a. LLM call counter — timeline views

The `gemini_usage.json` ledger already records every call with `ts`, `model`, `tokens`, `cost`. The UI just doesn't surface it.

Need three aggregation views:

| View | Bucket | Purpose |
|---|---|---|
| Today | By hour | See if a run is burning budget right now |
| Last 7 days | By day | Day-over-day spending trend |
| Last 30 days | By day | Monthly burn rate |

### 6b. Cost same aggregation as call counter

- Per-model breakdown: `gemini-3.7-flash: 42 calls, $0.0032` / `gemini-3.1-pro: 8 calls, $0.0180`.
- Per-model cost per time bucket.
- Total: `$0.0212 today · $0.15 this week · $0.42 this month`.

### 6c. Backend needs

- New endpoint: `GET /api/usage?period=today|week|month` — returns bucketed calls + costs.
- Reuse `gemini_usage.py` and `_load()` which already has everything.
- Frontend: a simple bar chart (or at minimum a table) in a collapsible panel below the budget badge.

---

## Design Note: Rework UI Against a Stub

Per the Patchee UX decision (Phase 11 in patchee-sandbox/REBUILD_PLAN.md), the same approach applies here:

> Build the dashboard against a **scripted stub** — a fake in-memory run that auto-advances through states/turns on a timer. Reconnect the real engine only after every UX state is visually obvious.

---

## Sequence (when we start fixing)

1. Duplicate check (#4) — prevents data mess before anything else.
2. Delete idea (#5) — cleanup hygiene.
3. "Start debate" feedback (#1) — stops the most common user mistake.
4. Turn/progress indicator (#2) — makes the debate visible.
5. "Rebut" option (#3) — adds the missing third flow.
6. Usage/cost visibility (#6) — last because it's already tracked, just not rendered.