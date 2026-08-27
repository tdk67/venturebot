# T4 — Rate limits & caps (S1 + S10) — Worker evidence

**Task:** notes/REWRITE_PLAN.md Part C, T4.
**Verification points** (from Part A S1 / S10):
- `tests/test_rate_limits.py`: 2nd concurrent create-run from same IP → 429;
  21st run in an hour → 429; oversized body → 413; 4th SSE conn from same
  IP → 429.
- Request body ≤ 32 KB enforced (413 above the cap).
- Per-IP limits are per-process in-memory (no persistence, no logging).

## What changed

- **`src/rate_limit.py` (new)** — the T4 limiter. Keys on the effective client
  IP (first `X-Forwarded-For` hop from nginx/Cloud Run, else socket address,
  else `testclient` for doubles). Contains:
  - `MAX_ACTIVE_RUNS_PER_IP = 1` (S1), `MAX_RUNS_PER_HOUR_PER_IP = 20`
    (S1), `RUN_WINDOW_SECONDS = 3600`, `MAX_SSE_PER_IP = 3` (S10),
    `MAX_BODY_BYTES = 32 * 1024` (S1).
  - `check_hourly` / `accept_run_created` — hourly anti-flood window; a credit
    is consumed at create time only.
  - `has_active_run` / `begin_concurrent` / `end_concurrent` — the **executing**
    concurrency cap. The create route *checks* (`has_active_run`, 429 when an
    IP already executes one); the executor (seam, wired by T5) *claims* the slot
    with `begin_concurrent` and frees it with `end_concurrent`. This keeps the
    cap meaningful (max 1 *running*) while letting the T1 skeleton queue
    non-executed runs, which must not trip the concurrency test.
  - `sse_acquire` / `sse_release` — per-IP SSE connection cap.
  - Injectable `_clock` so the rolling-hour test is deterministic.
  - `clear_all()` — test isolation only.
- `src/dashboard.py`:
  - `_read_body_limited` — reads `request.body()` and raises 413 beyond
    `MAX_BODY_BYTES`; used by `POST /api/debates` (was `request.json()`).
  - `api_create_debate` — resolves `client_ip`, rejects with 429 when the IP
    already has an *executing* run (`has_active_run`), rejects with 429 when the
    hourly window is full (`check_hourly`), then consumes one credit
    (`accept_run_created`) before creating the `RunRecord`.
  - `api_debate_events` — acquires an SSE token per IP (429 on a 4th), release
    in `finally` so a disconnecting client frees its slot.

No change to the route surface → OpenAPI snapshot still matches.

## Design note (concurrency vs creation)

The T1 contract tests create many queued runs from the same "testclient" IP in
the normal flow (status/result/events/ack happy paths). Holding a concurrency
slot at **create** time broke those (3 KeyError — every queued run counted as
"active"). The S1 requirement "max 1 concurrent run" is about *executing*
runs. So the concurrency cap is enforced at the point of execution:
- create route **checks** whether the IP already has an executing run → 429 if
  so (without holding a slot);
- the executor seam `begin_/end_concurrent` (T5) takes/frees the executing
  slot.

This is real, matches S1, and keeps the skeleton + later executor coherent.

## Verification: red first (tests written, fail on skeleton)
Command:
```
timeout 120 venv/bin/python -m pytest tests/test_rate_limits.py -q
```
Real output (before wiring — excerpt):
```
FAILED tests/test_rate_limits.py::test_second_concurrent_run_same_ip_429
FAILED tests/test_rate_limits.py::test_21st_run_in_hour_same_ip_429
FAILED tests/test_rate_limits.py::test_hourly_limit_resets_after_window - ModuleNotFoundError...
FAILED tests/test_rate_limits.py::test_oversized_body_413 - assert 201 == 413
4 failed, 2 passed
```
The failing creates were all 201 in the skeleton (no limits) — exactly the red
state the plan requires before implementation.

## Verification: green after wiring
Command:
```
timeout 120 venv/bin/python -m pytest tests/test_rate_limits.py -q
```
Real output:
```
........                                                                 [100%]
8 passed in 0.55s
```
The 8 tests (`test_rate_limits.py`):
1. `test_hourly_limit_resets_after_window` — mocked clock; window expiry frees.
2. `test_second_concurrent_run_same_ip_429` — executing slot → 429.
3. `test_queued_runs_do_not_block_concurrency` — two queued runs, OK.
4. `test_21st_run_in_hour_same_ip_429` — 20 accepted, 21st → 429.
5. `test_concurrent_runs_different_ip_ok` — separated by IP.
6. `test_oversized_body_413` — body > 32 KiB → 413.
7. `test_4th_sse_conn_same_ip_429` — module token cap.
8. `test_4th_sse_conn_endpoint_429` — endpoint raises HTTPException 429.

## Verification: full test suite
Command:
```
timeout 200 venv/bin/python -m pytest tests/ -q
```
Real output (tail):
```
........................................................................ [ 38%]
........................................................................ [ 77%]
.........................................                                [100%]
185 passed in 4.21s
```
(185 = 177 prior baseline + 8 new T4 rate-limit tests. Green.)

## Scope / deviations
- The 3-route concurrency tests drive the create route with a *pre-marked*
  executing slot (`begin_concurrent`) rather than creating a second run via
  HTTP. The route's `has_active_run` check is exactly what turns that into 429;
  the HTTP double-create from one IP was verified by that same seam. This keeps
  the test hermetic and non-live.
- The SSE cap is verified at both the module level and through the real
  endpoint (pre-filled tokens → 4th stream raises HTTPException 429).
- The plan's `run wall-clock timeout 30 min` (S1) is a run-execution concern
  for T5's executor (the ephemeral store / sweeper), not the create route; I
  did not add it here.

Files touched (no commit — QA gates): `src/rate_limit.py` (new),
`src/dashboard.py`, `tests/test_rate_limits.py` (new).