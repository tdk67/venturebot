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

## 2026-08-27 — T2 done (Orchestrator hardening: per-agent lifecycle events + loud run_failed)
- What changed: `src/agents/orchestrator.py` now emits `agent_started`/`agent_finished`
  (agent name, model, duration, run_id) around every sub-agent call in `_run_sub_agent`;
  `run_orchestrator`'s `except Exception` block emits `run_failed` with reason before
  archiving. `_run_sub_agent` gained a `run_id` kwarg; all `OrchestratorTools` call sites
  pass `run_id=self.run_id`. `run_orchestrator` gained `external_run_id` for deterministic
  testing (additive, no breaking change). `src/events.py` gained a per-run sink registry
  (`register_run_sink`/`unregister_run_sink`) so typed events route by run_id (D4: no
  cross-run broadcast).
- Evidence: notes/evidence/T2-worker.md + notes/evidence/T2-qa.md.
- Tests: tests/test_orchestrator_errors.py (3) + full suite `171 passed`.
- Lesson learned: sync callbacks in async event buses fire synchronously as part of the
  `cb(event, payload)` call before `loop.create_task(None)` raises — the event bus is
  fire-and-forget, so callback failures don't break the debate.
- Commit: eb7ba4b.

## 2026-08-27 — T3 done (BYOK plumbing: memory-only keys, redaction, no server-key fallback)
- What changed: `src/dashboard.py` gained `_redact`, `_redact_dict`, `_scrub_key_from_run`,
  `_orchestrator`, and `_run_debate` — the full BYOK plumbing. User keys arrive per-request,
  held in memory only, passed to the orchestrator's factory, and discarded in `finally`.
  All error/event text is redacted before storage. No server-key fallback exists (D1).
  `_run_debate` is the per-run executor (not yet wired into the route — that's a later task).
- Evidence: notes/evidence/T3-worker.md + notes/evidence/T3-qa.md.
- Tests: tests/test_key_canary.py (6) + full suite `177 passed`.
- Lesson learned: pre-commit hooks can false-positive on test fixtures using realistic-looking
  key patterns (e.g., `AIza...`); use obviously-fake values like `FAKE-SERVER-KEY-GOOGLE` instead.
- Commit: 3aa4c1b.

## 2026-08-27 — T4 done (Rate limits & caps: per-IP concurrency, hourly anti-flood, body size, SSE cap)
- What changed: `src/rate_limit.py` (new) — in-memory per-IP rate limiter with
  `MAX_ACTIVE_RUNS_PER_IP=1`, `MAX_RUNS_PER_HOUR_PER_IP=20`, `MAX_BODY_BYTES=32KB`,
  `MAX_SSE_PER_IP=3`. Injectable clock for deterministic tests. `clear_all()` for
  test isolation. `src/dashboard.py` gained `_read_body_limited` (413 on oversized
  body), rate-limit checks in `api_create_debate` (429 on concurrent or hourly limit),
  and SSE token acquire/release in `api_debate_events` (429 on 4th conn). Concurrency
  model: create route *checks* `has_active_run` (non-consuming); executor seam
  `begin_concurrent`/`end_concurrent` (T5) takes/frees the executing slot.
- Evidence: notes/evidence/T4-worker.md + notes/evidence/T4-qa.md.
- Tests: tests/test_rate_limits.py (8) + full suite `185 passed`.
- Lesson learned: concurrency cap at create time breaks queued-run skeletons;
  enforce at executor seam instead, check at create.
- Commit: 58aac99.

## 2026-08-27 — T5 done (Ephemeral store + TTL sweeper + ACK lifecycle)
- What changed: `src/ephemeral_store.py` (new) — `EphemeralStore` class with TTL-based
  lifecycle (24h default), ACK tracking, tombstone for 410 Gone responses. Per-IP
  concurrency tracking via `_active_runs` dict. `sweep_expired()` removes old records
  and workspaces. `src/inflight_sweeper.py` (new) — `sweep_run_workspace()` wipes a
  run's workspace dir (path-traversal guarded: only `runs/{run_id}/` under WORKSPACE_DIR),
  `sweep_workspaces()` batch helper. `src/dashboard.py` gained module-level `STORE`
  singleton, `_sweep_once()` (sweeps store + workspaces), ASGI lifespan context manager
  (60s sweep interval, production only), `_emit_plugin()` for sweep notifications.
  `api_create_debate` registers in STORE, `_lookup()` returns 410 Gone for tombstoned
  runs, `api_get_result` checks STORE, `api_ack_result` calls `STORE.ack()` and sweeps
  workspace. Old `_RUNS` dict retained as SSE replay cache.
- Evidence: notes/evidence/T5-worker.md + notes/evidence/T5-qa.md.
- Tests: tests/test_ephemeral.py (8) + tests/test_result_ack.py (7) + full suite `200 passed`.
- Lesson learned: ASGI lifespan context (`@asynccontextmanager` on `app.router.lifespan_context`)
  runs in production but not in `TestClient()` without explicit lifespan support — tests
  must inject a fake clock (`STORE.tick`) and call `_sweep_once()` directly to avoid
  non-deterministic timing.
- Commit: 2f9e013.
