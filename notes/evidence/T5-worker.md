# T5 — Evidence: Ephemeral store + TTL sweeper + ACK

**Task:** REWRITE_PLAN.md Part C, T5 (S6, S7, D2, D3).
**Date:** 2026-08-27
**Status:** PASS — all verification points green, full suite green.
**No commit/push** (delegated to QA).

## What changed

New files:
- `src/ephemeral_store.py` — `EphemeralStore`: in-memory, TTL-bounded (24 h), ACK-aware
  per-run store. No idea table, no listing/enumeration surface (D2/S3). Tombstones
  (`_gone`, run_ids only, no content) let the API answer **410** for acked/expired
  runs instead of 404. `sweep_ttl(now=...)` with injectable `.tick` clock.
- `src/inflight_sweeper.py` — `sweep_run_workspace` / `sweep_workspaces`: wipe a
  run's `workspace/runs/{run_id}/` dir (S6). Path-traversal guard: only a direct
  child of `runs/` is ever touched.

`src/dashboard.py`:
- `STORE` module-level store (tests swap it for a fresh store per test).
- `_emit_plugin` — fire-and-forget `expired` event the store fires on sweep.
- `_sweep_once()` — synchronous sweep primitive (store + `_RUNS` + workspace);
  drives the production ASGI lifespan loop every 60 s.
- `api_create_debate` → registers in STORE.
- `api_get_result` → reads STORE; 410 if gone, 404 if unknown.
- `api_ack_result` → ACK wipes the store record + `_RUNS` + workspace → 410.

## Verification point: S7 / D3 (result held until ACK; 410 after ACK)

`tests/test_result_ack.py` (9 tests):
- `test_result_survives_disconnect_reget_200` — drop client, re-GET → 200.
- `test_result_after_ack_gone_410` — after ACK, result → 410; store empty.
- `test_ack_before_result_not_ready_409` — ACK before result → 409, NOT wiped.
- `test_ack_unknown_id_404`, `test_expired_result_gone_after_sweep` (→410,
  never 404), `test_fresh_result_survives_sweep`.

## Verification results (S6 / D2 — ephemeral, no left-behind idea text)

`tests/test_ephemeral.py` (8 tests):
- `test_store_has_no_idea_table` — no idea table / no listing (D2/S3).
- `test_workspace_wiped_after_sweep` — forensic `grep -r` of the workspace tree
  finds ZERO matches for the idea text after the workspace is swept.
- `test_dashboard_sweep_wipes_workspace_and_410s` — end-to-end: the periodic
  `_sweep_once()` removes the store record, the `_RUNS` in-memory entry and the
  on-disk workspace; result endpoint → 410.
- TTL store semantics with the fake clock (`test_store_register_get_sweep_ttl`,
  `test_store_keeps_fresh_run`, `test_ack_removes_entry`, `test_ack_nonexistent_false`).

## T5 targeted run (real output)

```
$ timeout 120 venv/bin/python -m pytest tests/test_ephemeral.py tests/test_result_ack.py -v
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0 -- /root/venturebot/venv/bin/python
cachedir: .pytest_cache
rootdir: /root/venturebot
configfile: pytest.ini
plugins: anyio-4.14.2
collecting ... collected 15 items

tests/test_ephemeral.py::test_store_has_no_idea_table PASSED           [  6%]
tests/test_ephemeral.py::test_workspace_wiped_after_sweep PASSED       [ 13%]
tests/test_ephemeral.py::test_store_register_get_sweep_ttl PASSED      [ 20%]
tests/test_ephemeral.py::test_store_keeps_fresh_run PASSED             [ 26%]
tests/test_ephemeral.py::test_ack_removes_entry PASSED                 [ 33%]
tests/test_ephemeral.py::test_ack_nonexistent_false PASSED             [ 40%]
tests/test_ephemeral.py::test_unknown_run_id_not_enumerable PASSED     [ 46%]
tests/test_ephemeral.py::test_dashboard_sweep_wipes_workspace_and_410s PASSED [ 53%]
tests/test_result_ack.py::test_result_survives_disconnect_reget_200 PASSED [ 60%]
tests/test_result_ack.py::test_result_after_ack_gone_410 PASSED        [ 66%]
tests/test_result_ack.py::test_ack_before_result_not_ready_409 PASSED  [ 73%]
tests/test_result_ack.py::test_ack_unknown_id_404 PASSED               [ 80%]
tests/test_result_ack.py::test_expired_result_gone_after_sweep PASSED  [ 86%]
tests/test_result_ack.py::test_fresh_result_survives_sweep PASSED      [ 93%]
tests/test_result_ack.py::test_sweep_removes_only_expired PASSED       [100%]

============================== 15 passed in 1.44s ==============================
```

## Full suite — real output

```
$ timeout 300 venv/bin/python -m pytest tests/ -q
........................................................................ [ 36%]
........................................................................ [ 72%]
........................................................              [100%]
200 passed in 3.45s
```

- Before T5: 185 passed. After T5: **200 passed** (15 new T5 tests, 0 regressions).
- OpenAPI snapshot test still passes unchanged (no API surface added/removed).
- No new real secret key patterns in the diff (secret-scan of new files CLEAN).

## Deviations (none that change contract)

- `STORE.register` dropped the `ip=` param — T4's concurrency cap already tracks
  active runs at the executor seam; T5 keeps the store IP-agnostic and simple.
- The production TTL loop runs only under the ASGI lifespan (uvicorn / context
  TestClient); plain `TestClient()` puts it not on (keeps TTL tests deterministic).