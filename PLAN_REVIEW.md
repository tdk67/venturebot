# Implementation Plan — Strict Review

**Reviewer:** pi (coding agent)
**Date:** 2026-08-19
**Reviewed docs:** `PRD.md` (VB-PRD-2026-08-18), `IMPLEMENTATION_PLAN.md` (2026-08-19)
**Also checked:** actual code in `~/venturebot/`, `.env`, `.env.example`, and the referenced ADK samples under `/root/patchee-sandbox/adk-samples/`.

---

## Verdict

The plan is **structurally complete** — tasks, priorities, a dependency graph, a
risk register, a testing strategy, and a demo checklist are all present. That is
better than most hackathon plans. But it is **not executable as written**:

1. It **self-reports Phase 2 as "✅ Working/Complete" when it is not** (the budget
   NFR is unimplemented).
2. It contains **two directly contradictory design decisions** (Task 6 vs Task 7
   on the verdict gate).
3. It cites **reference files that exist but whose patterns do not do what the
   plan claims** (HITL clarify, `after_agent_callback` on `SequentialAgent`).
4. It **prescribes wrong fixes** for the search tool and the API-key wiring.
5. It ships **live secrets in `.env`** and has **no git hygiene**.
6. Many "verification steps" are **prose assertions, not runnable checks**, and
   the eval suite is **internally inconsistent**.

---

## A. What's good (credit where due)

- Task granularity (23 tasks) with per-task files, reference paths, acceptance
  criteria, and dependencies is solid.
- The **"Fail Loud, Fail Honest"** section (§1.5) is excellent and genuinely
  de-risks the demo — but see D5: the rest of the plan doesn't consistently honor it.
- The dependency graph correctly identifies the critical path
  `0→1→…→8→9→10→23`.
- The existing `llm_client.py` / `agents.py` / `venturebot_harness.py` /
  `sim_store.py` code is clean and coherent.

---

## B. Critical factual errors (will break the build or mislead debugging)

### B1. `google_search` fix instructions are wrong
The plan (R2, Task 0, Task 23.2) repeatedly instructs to "enable Custom Search
API in GCP project" when `google_search` fails. The ADK built-in tool
`from google.adk.tools import google_search` is **Gemini's built-in Search
grounding**, not the Custom Search JSON API / Programmable Search Engine. The
real prerequisites are a Gemini model that supports search grounding and a valid
`GOOGLE_API_KEY` (AI Studio) or Vertex auth. The fix instruction sends the
operator down the wrong path on demo day.

### B2. API key name mismatch — Phase 1 will not authenticate
- `.env` contains `GEMINI_API_KEY` (an AI-Studio-style `AQ.…` key).
- ADK's `Gemini` model reads `GOOGLE_API_KEY` (or `GOOGLE_GENAI_USE_VERTEXAI`),
  **not** `GEMINI_API_KEY`.
- Task 0 installs `aiosqlite sse-starlette apscheduler jinja2` — **never
  `python-dotenv`**, yet Task 6.2 says `research_debate/__init__.py` will
  "Load .env file".
- `config.py`'s `resolve_api_key()` only resolves `OPENROUTER_API_KEY`; nothing
  loads the Gemini key for Phase 1.

Net: Phase 1 has **no working path from `.env` → ADK model**. P0 gap, unlisted
in the environment tasks.

### B3. `.env` / `.env.example` are Patchee leftovers — with live secrets
Both files still contain Patchee project config (`GCP_PROJECT_ID=patchee-dev`,
`MODEL_PROSECUTOR/MODEL_DEFENSE/MODEL_JUDGE`, `FIRESTORE_COLLECTION`, `BIGQUERY_*`),
**plus a real `GEMINI_API_KEY` and a real fine-grained `GITHUB_PAT` in plaintext**.
None of the `MODEL_*` names match what VentureBot's `config.py` actually reads
(`VENTUREBOT_MODEL_*`). Plan §1.1 claims `.env` is "Present, GEMINI_API_KEY set,
models configured" — **the models configured are the wrong ones**, and the file
is a credential leak. Scrub and replace before any push.

### B4. `shared_state.json` is claimed to exist but doesn't
PRD Appendix B and the plan both treat `shared_state.json` as present. `ls`
confirms it does **not** exist. Also PRD §10.1 places Phase 2 files under
`blind_tdd/`, but they actually live at the **repo root** (plan §1.1 lists them
correctly at root). The PRD layout is wrong, and `bridge.py` /
`unified_dashboard.py` need a decision on which layout is canonical.

---

## C. Direct internal contradictions

### C1. Verdict gate: Task 6 vs Task 7 disagree (biggest design gap)
- **Task 6** ("Critical Design Decision") recommends **Option A**:
  `SequentialAgent` always runs all 5 agents; PRD Writer's instruction says
  *"If verdict is PRUNE, output 'PRUNE — no PRD needed' and stop."*
- **Task 7** (and PRD §4.2) specify a **real HITL verdict gate**: *"If any score
  < 6, emit input-required with verdict + [PROCEED ANYWAY][ABORT] buttons."*

Mutually exclusive. Option A produces no pause, no buttons, no conditional
routing. The plan never resolves which mechanism ships, and Task 7's acceptance
criteria cannot be satisfied by Task 6's Option A wiring. This is the core demo
feature and it is underspecified-to-contradictory.

### C2. The `clarify_question` pseudocode cannot work
Task 7.1 gives:
```python
def clarify_question(question: str) -> str:
    return question  # shown to the human
```
ADK tool HITL does **not** work by "return the question." The plan claims to
follow PRD §4.1's `long_running_operation` / `input_required` mechanism, but the
pseudocode uses neither. The cited references (`ask_parent.py`,
`delegate_runner.py`) are **A2A subagent-escalation patterns**, not ADK
tool-level HITL. There is **no working reference in the sample set for an ADK
`input_required` tool** (the real machinery is `LongRunningFunctionTool` +
EUC/`input-required` events). 2h for "HITL Gates" is not credible against an
unproven mechanism.

### C3. `after_agent_callback` on `SequentialAgent` is punted, not decided
Task 14.2 literally says *"Or wire per-sub-agent if SequentialAgent doesn't
support it."* The cross-session-memory sample wires `after_agent_callback` on a
root **`Agent`**, not a `SequentialAgent`. No spike/verification step determines
whether the callback fires per-sub-agent or once per chain — so Fork 1 rests on
an unverified API assumption.

---

## D. Verification steps: mostly assertion, not verification

### D1. Acceptance criteria are prose, not runnable checks
Most ACs are of the form *"produces a brief with required sections,"* *"scores
are integers 1-10."* No assertion, threshold, or fixture is specified. The
Testing Strategy (§9.1) names test files (`tests/test_researcher.py`, etc.) but
**none exist** and no task creates them. The loop between "AC" and "automated
test that proves it" is never closed.

### D2. Eval suite is internally inconsistent
- PRD §9 defines **E-01…E-08** (including E-06 self-improvement, E-07 dream
  review, E-08 shadow-mode).
- Plan §9.3 lists only **E-01…E-05**, dropping the three that matter most for
  Milestones 3 & 4.
- PRD §9.3 references `agents-cli eval run`; the plan references `pytest` — two
  different eval runners, no reconciliation, and `agents-cli` appears nowhere in
  env setup.

### D3. E-05 (budget) is unverifiable — the mechanism doesn't exist
E-05 asserts a *"rate limiter interceptor blocks further LLM calls"* on a $2.00
budget. But:
- `llm_client.py` has **no cost tracking, no budget check, no rate limiter**.
- `config.py` defines `DAILY_BUDGET_LIMIT_USD = 2.00` — **never referenced
  anywhere in the codebase.**
- NFR-1 (budget control ≤ $2.00, rate limiter enforces) and NFR-10 (every LLM
  call has explicit timeout + max_tokens) are therefore **partially
  aspirational**: timeout/max_tokens exist, budget enforcement does not.

This proves the biggest meta-problem: **§1.1 declares Phase 2 "✅
Complete/Working," but Phase 2 does not meet the PRD's own NFR-1.** "Already
built" ≠ "meets spec."

### D4. Unit tests will be live, non-deterministic, and billed
Tests like *"Critic uses google_search"* will hit live APIs: flaky, costly,
non-hermetic. There is **no mocking/fixture strategy** for `google_search` or
Gemini, and no tolerance for non-deterministic LLM output in the assertions.

### D5. "Fail loud" is not honored in the demo checklist
§1.5 forbids silent degradation. Yet §10 (During Demo) says: *"If google_search
fails: point out that Critic can still reason from the brief."* That is exactly
the silent degradation §1.5 forbids. The demo script undercuts the stated
philosophy.

---

## E. Unclear / under-specified actions

| # | Issue |
|---|-------|
| E1 | **"Pin to the version used by adk-samples"** (R1, Task 0) — no concrete version number. `google-adk` is not installed, so there is no lockfile to copy. "Pin to X" is not executable without X. |
| E2 | **Tooling inconsistency: `uv` vs `venv/pip`.** PRD §10.1 runs `uv run adk web …`; plan Task 0 uses `./venv/bin/pip install …`. `uv` is never installed or mentioned. Pick one. |
| E3 | **Model slugs unverified and internally inconsistent.** `gemini-3.1-pro` (config.py) vs `gemini-3.6-flash` (`.env`); `deepseek/deepseek-v4-pro` and `deepseek-chat-v3-0324` are dubious/dated. Task 0.4 only instantiates one model and prints the object — it does not validate that the model is callable/exists/returns a response. |
| E4 | **HITL answer reinjection is hand-waved.** Task 9/10 never specifies how `/api/clarify-response` and `/api/verdict-action` resume the paused `runner.run_async()` generator (the genuinely hard part of SSE + HITL). |
| E5 | **Phase 1 resumability is missing.** PRD §12.2 lists `ResumabilityConfig`; NFR-3 promises inspectable/resumable runs; no task implements ADK session resumability. A demo crash mid-debate = total restart. |
| E6 | **dream_review scheduler specified twice** (Task 16 local APScheduler, Task 22 Cloud Scheduler) with no statement of which is authoritative for the demo. |
| E7 | **No `.gitignore`, no git repo** (verified: `not a git repository`). Post-demo "push all code to GitHub" would currently commit `state.json`, `workspace/`, `.pytest_cache/`, and plaintext secrets in `.env`. |
| E8 | Minor: Task 3 header typo "CriticAgent"; plan total 36h vs PRD ~35h (drift shows the two docs weren't reconciled). |

---

## F. Fix list (priority ordered)

### P0 — blocks execution, fix now
1. Resolve the **Task 6 vs Task 7 verdict-gate contradiction** — pick one
   mechanism and rewrite both tasks to agree.
2. Fix **API-key wiring** (`GOOGLE_API_KEY` vs `GEMINI_API_KEY`, add
   `python-dotenv`, load Gemini key in Phase 1).
3. **Scrub `.env`/`.env.example`** of Patchee config + live secrets; add
   `.gitignore`; decide the `blind_tdd/` vs root layout.
4. Correct the **`google_search` error-handling instructions** (built-in search
   grounding, not Custom Search API).

### P1 — correct the record
5. Stop marking Phase 2 as "complete" — implement or explicitly descope the
   **budget/rate-limiter** (NFR-1, E-05).
6. Add a **real, runnable verification step per task** (assertion + fixture +
   mock for `google_search`), not prose ACs.
7. Reconcile the **eval suite** (E-01…E-08, one eval runner).

### P2 — de-risk
8. Add a **spike task** for ADK `input_required` HITL and
   `after_agent_callback`-on-`SequentialAgent` before Task 7/14 may start.
9. Validate the **exact model slugs** against live catalogs.
10. Align the "fail loud" demo script with §1.5.

---

**Summary:** strong scaffold, but its confidence ("Ready to execute") is higher
than the evidence supports. The two contradictions (C1) and the "working Phase 2
that isn't" (D3) are the ones that will surface as demo-day failures.
