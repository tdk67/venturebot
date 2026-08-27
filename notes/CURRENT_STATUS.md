# VentureBot — CURRENT STATUS (single source of truth)

**Written:** 2026-08-27 (after memory review + code/doc audit)
**Read this first before touching anything in this repo.**

## 0. NEXT SESSION PICKUP (handoff 2026-08-27)

The detached worker/QA workflow is BUILT and SMOKE-TESTED (`pi -p` unattended
verified 2026-08-27; harness lives in `~/pi-workflow/`, own git repo).
As the new session: read `AGENTS.md` rules, then:

1. The next action is **task T1** (board: pending, all blocking items cleared
   except the confirmations below).
2. Open decisions D2/D3/D5/D6: ask the user to confirm ONCE. If the user says
   "proceed" without changes, adopt the proposed answers recorded in
   REWRITE_PLAN.md Part B and mark them LOCKED in TASKBOARD.md.
3. Then execute strictly via the harness: `~/pi-workflow/run_task.sh
   ~/venturebot T1` → wait for `logs/tasks/T1.worker-done` →
   `~/pi-workflow/run_qa.sh ~/venturebot T1` → `board.sh`. Repeat per task.
4. Everything else (statuses, evidence, memory) is maintained by the agents in
   TASKBOARD.md / notes/evidence/ / JOURNAL.md — keep this file updated only
   for coordinator-level changes.

---

---

## 1. Where the code stands

- **`main` (HEAD = `6f57dbe`) is the BROKEN app.** It was explicitly declared
  unusable on 2026-08-27. The same state is preserved on branch
  **`app-broken-27-aug`**.
- **`poc-0-0-1`** branch = the last stage where the debate more or less worked.
- Tests pass (155 passed) — **green tests do NOT mean working product**.
  Verified broken behavior still present on `main`:
  - `dashboard.py::_run_phase1_loop` has **no try/except around
    `run_orchestrator`** → any exception kills the debate silently; no
    `run_finished`/error event ever reaches the UI. Commit `6f57dbe` fixed one
    import-level silent death, not the structural problem.
  - `static/app.js` SSE `es.onerror` is a no-op; there is no `run_failed`
    event type emitted anywhere → the UI just sits in "thinking" forever.
  - Orchestrator thinking >4 min with zero tool-call visibility in the UI
    (sub-agent calls not surfaced) — user-reported, unresolved.

## 2. The decided path: COMPLETE REWRITE (decision of 2026-08-27)

Decisions recorded in the last working session (session ended by **timeout**
~08:40; not all of it was written down — this file reconstructs it from memory):

1. Start from zero. Rewrite into a **TypeScript frontend + near-stateless
   backend**. Old Python backend code is knowledge, not copy-paste source.
2. **BYOK-style frontend** — users bring their own API keys; ideas and history
   are stored **client-side**; export/import is the user's backup mechanism.
3. Backend is NOT fully stateless: it must make sure the user **downloaded the
   result**; behavior on client/server disconnect mid-debate was still an open
   brainstorm question when the session died.
4. The **orchestrator + agentic loop must survive the rewrite** — but fixed:
   it must give feedback to the UI (show what each sub-agent is doing), and it
   must not be a deterministic sequential pipeline.
5. Before coding: write an **implementation plan with actionable tasks and
   verification points**; decide which current tests can be reused; **fix the
   verification plan before starting to code**. Stay in brainstorm mode for
   pros/cons of each change.
6. GCP deployment stays the target: Firebase Hosting mapping **idea-lint.my**
   to the Cloud Run service (project `venturebot-506408`).

**Nothing from the rewrite has been committed yet.** The timed-out session
produced no code on disk (verified: clean tree, no TS files).

## 3. Domains & deployments

| What | Where | Status |
|---|---|---|
| **idea-lint.my** | GCP Cloud Run (`venturebot-506408`) via Firebase Hosting rewrites | **The hackathon target.** Firebase hosting deployed & reachable; login at this domain was broken (drove the rewrite). |
| **venturebot.taskmind-ai.com** | **This VPS** (systemd `venturebot.service` + nginx, A record 187.124.171.89) | Legacy deployment of the broken app. **May stay running, but is NOT needed for the hackathon. Do NOT add it to the README.** Kept in memory/this file only. |

✅ **VPS auth decided and flipped (2026-08-27): `VENTUREBOT_NO_AUTH=1`.**
Login never worked reliably; the new approach needs no authentication
(BYOK + client-stored data). VPS verified open, HTTPS 200.
Auth history (closed chapter): 8/20 GIS → 8/23 sessions+OAuth code flow
A5/A6 → 8/26 NO_AUTH → 8/27 dead end. **No more auth work on the old app.**

⚠️ Known live exposure on the VPS while it stays up: under NO_AUTH the old
app exposes `/api/budget/raise` and server-key compute to anyone (details:
REWRITE_PLAN.md §A2, mitigation proposal R1 pending user OK).

## 4. Docs inventory

- **Obsolete change plans → renamed `*.md.keep`** (14 files: multi-user design
  & tasks, bearer-token architecture, public deployment design, old backlog,
  old implementation plans, firebase hosting doc, loop analysis v1, phase-2).
  Each carries an OBSOLETE stamp. Do not implement from them.
- **Still valid / reference:** `PRD.md`, `Review-PRD.md`,
  `LOOP_ARCHITECTURE_V2.md` (orchestrator design = rewrite knowledge),
  `ORCHESTRATOR_IMPLEMENTATION.md` (documents current loop internals),
  `PARKED_IDEAS.md`, `CLAIMS_VS_REALITY.md` (privacy claims = prio #1 to
  revisit once things work), competitor/market research files,
  `GCP_DEPLOYMENT.md` (still needed), `DEPLOYMENT.md` (legacy VPS how-to).

## 5. Next actions (in order)

1. **Lock D1–D6 in `notes/REWRITE_PLAN.md`** (proposed answers inside).
2. Follow REWRITE_PLAN.md strictly: contract tests written FIRST, then code.
3. Implement rewrite; UI must show per-agent progress and explicit errors.
4. Deploy to GCP behind idea-lint.my (T12-T13).
5. Pass the 5-scenario global acceptance gate (REWRITE_PLAN Part D).
6. Revisit privacy claims (CLAIMS_VS_REALITY) — prio #1, covered by T11.
