# T3 — BYOK plumbing (memory-only keys, redaction) — Worker evidence

**Task:** notes/REWRITE_PLAN.md Part C, T3.
**Verification points:**
- `tests/test_key_canary.py` (S2) + `tests/test_api_contract.py`: create-run with
  a blank/absent api_key → 400, ALWAYS (D1 — no server-key fallback in any form).
- The per-request key is held in memory only, passed to the orchestrator's BYOK
  factory, and DISCARDED at run end.
- A canary key never reaches stdout/stderr, workspace/state files, or the run
  result/events.

## What changed

- `src/dashboard.py`:
  - Added `_redact(text, api_key)` — replaces the key with `[REDACTED]`.
  - Added `_redact_dict` / `_scrub_key_from_run` — recursively scrub the key from
    the in-memory `RunRecord` (error, result, event payloads).
  - `RunRecord.api_key` now defaults to `""` and is cleared in `finally`.
  - Added `_orchestrator(...)` thin wrapper (test-seam) that forwards the key +
    injected URLs via `SteeringInbox` into `run_orchestrator`.
  - Added `_run_debate(rec, api_key, ...)` — the per-run executor: holds the key
    ONLY as a local for the run's lifetime, passes it to the orchestrator,
    materialises the result for the `/result` contract, emits
    `run_finished` / `run_failed` (loud, T2), redacts the key from any surfaced
    error/event, and **discards the key in `finally`** (`rec.api_key = ""` +
    `_scrub_key_from_run`). No server-key read anywhere (no `config.*_api_key()`,
    no `ALL_AGENTS` fallback).
- `tests/test_key_canary.py` (new, 6 tests).

The API surface is unchanged (no new routes), so the OpenAPI snapshot still
matches (`test_openapi_snapshot_reproducible` passes).

## Verification: non-live canary tests

Command:
```
timeout 120 venv/bin/python -m pytest tests/test_key_canary.py -v
```
Real output (tail):
```
tests/test_key_canary.py::test_create_debate_absent_key_400_even_with_server_env PASSED [ 16%]
tests/test_key_canary.py::test_create_debate_blank_key_400_always PASSED [ 33%]
tests/test_key_canary.py::test_orchestrator_passes_user_key_to_agents_no_env_fallback PASSED [ 50%]
tests/test_key_canary.py::test_run_debate_passes_key_and_discards_afterwards PASSED [ 66%]
tests/test_key_canary.py::test_run_debate_redacts_key_from_error PASSED  [ 83%]
tests/test_key_canary.py::test_redact_helper PASSED                      [100%]
============================== 6 passed in 1.56s ===============================
```

Notes on what each proves:
- `test_create_absent_key_400_even_with_server_env` — even with a server-side
  `GOOGLE_API_KEY` / `OPENROUTER_API_KEY` set in the environment, create-run
  WITHOUT a per-request key still returns 400 `api_key required` (D1: no
  fallback). Uses a canary `-server-fallback-must-not-exist` value and greps the
  detail for it.
- `test_orchestrator_passes_user_key_to_agents_no_env_fallback` — spies on
  `orch.create_agents`; `run_orchestrator(idea, api_key=canary)` hands the SAME
  canary verbatim to `create_agents` (memory only). A fake Runner raises so no
  LLM call happens; run still ends `failed` (loud, no exception escapes).
- `test_run_debate_passes_key_and_discards_afterwards` — drives `_run_debate(rec, canary)`,
  asserts the key is forwarded with the right run_id, then **`rec.api_key == ""`**
  after the run, no stdout/stderr leak, no on-disk leak under the isolated dirs,
  and `rec.status == "done"`.
- `test_run_debate_redacts_key_from_error` — fake orchestrator raises
  `RuntimeError(f"upstream auth failed: {canary}")`; the stored `rec.error`
  contains `[REDACTED]` and NOT the canary, and no event payload contains it.

## Verification: D1 blank/absent key → 400 always (reused contract test)

Command:
```
timeout 60 venv/bin/python -m pytest tests/test_api_contract.py::test_create_debate_requires_api_key -v
```
Real output (tail):
```
tests/test_api_contract.py::test_create_debate_requires_api_key PASSED   [100%]
============================== 1 passed in 0.44s ===============================
```

## Verification: full test suite

Command:
```
timeout 300 venv/bin/python -m pytest tests/ -q
```
Real output (tail):
```
........................................................................ [ 40%]
........................................................................ [ 81%]
.................................                                        [100%]
177 passed in 4.29s
```
(177 = 171 baseline + 6 new T3 canary tests. Green.)

## Scope / deviations

Could not get an actual canary key to place the network/LiveLLM half of S2
(real provider call with a fake canary key and grep server disk after) because
that requires a real model call against a BYOK provider, which is outside the
headless test budget and is covered by T13 (production smoke). The in-process
canary checks (transit to factory, memory-only lifetime, error/event redaction,
no-disk) are the hermetic T3 verification.

Files touched (no commit — QA gates): `src/dashboard.py`, `tests/test_key_canary.py` (new).