# JOURNAL — VentureBot rewrite (append-only memory log)

Every completed task gets one entry here (written by the QA agent after PASS).
Coordinator-level decisions are journaled by the coordinator.

## 2026-08-27 — Setup decisions (coordinator)
- Auth abandoned permanently: VPS flipped to `VENTUREBOT_NO_AUTH=1` (login
  never worked; new approach needs no auth). No further auth work on old app.
- Server Gemini key REMOVED from VPS `.env` (R1): public access now costs us
  nothing; duplicate-check verified LLM-free (local token overlap), so nothing
  else needed the key.
- Locked: D1 (BYOK everywhere, no server-key fallback), D4 (per-run event
  channels; global broadcast = privacy violation, deleted).
- 14 obsolete plans renamed `*.md.keep`; REWRITE_PLAN.md is the single active
  plan with per-task verification points (tests exist before code).
- Workflow established: changes are executed by DETACHED pi worker + QA agents
  from the generic harness in **`~/pi-workflow/`** (run_task.sh / run_qa.sh /
  board.sh — intentionally NOT stored under this repo); coordinator session
  only starts/monitors. QA gates every commit+push and journals here.

## 2026-08-27 — Coordinator intervention (T1 unblock)
- T1 worker (first two launches) wrote the API skeleton + contract test but wrote
  `test_events_endpoint_serves_sse` using `TestClient.stream()` on an infinite
  SSE generator -> deadlock (proven: single-test run EXIT=124 under `timeout 30`).
  pi's `bash` tool has no default timeout, so the worker looped re-running the
  hung full suite without diagnosing it.
- Coordinator fixes (harness is generic infra in ~/pi-workflow, not repo code):
  run_task.sh + run_qa.sh now instruct agents to wrap any possibly-hanging
  command in `timeout` and treat a hang as a failure to fix.
- Coordinator also fixed two bugs IN THE TASK'S OWN TEST (not product code):
  (a) SSE test now drives the endpoint generator directly with a fake
  disconnect request (asserts hello frame + clean termination);
  (b) `test_no_list_endpoint` used `list - set` -> now `set - set`.
- Result: contract tests green; full suite now shows ONLY the 15 legacy failures
  that REWRITE_PLAN.md "Test reuse decision" says to DROP (auth/ideas store).
  Deleting those legacy test files remains T1 scope -> handed back to worker.

## 2026-08-27 — T1 done (API contract skeleton + delete legacy/admin routes)
- What changed: `src/dashboard.py` rewritten as near-stateless T1 skeleton
  (in-memory run registry, 8-route contract: create/status/events/result/ack/
  clarify/byok-verify/health); legacy admin/auth/ideas routes deleted (S5);
  strict CSP + security headers kept (S9). OpenAPI snapshot committed and
  asserted reproducible on change (`--regenerate-openapi`).
- Dropped tests: `test_auth_flow.py`, `test_idea_runs.py` (deleted routes).
- Evidence: notes/evidence/T1-worker.md + notes/evidence/T1-qa.md.
- Tests: tests/test_api_contract.py (40) + full suite `168 passed`.
- Lesson learned: `TestClient.stream()` deadlocks on an infinite SSE generator;
  drive the endpoint generator directly with a fake disconnect request instead.
- Commit: f1325a9.
