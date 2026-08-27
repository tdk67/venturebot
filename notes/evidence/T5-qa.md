# T5 QA — Adversarial Verification Report

**Date:** 2026-08-27  
**Verifier:** QA Agent (adversarial)  
**Task:** T5 — Ephemeral store + TTL sweeper + ACK (S6, S7, D2, D3)

## Verification Protocol

### 1. T5 Tests — Independent Run ✅

```
$ timeout 120 venv/bin/python -m pytest tests/test_ephemeral.py tests/test_result_ack.py -v
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0 -- /root/venturebot/venv/bin/python
cachedir: .pytest_cache
rootdir: /root/venturebot
configfile: pytest.ini
plugins: anyio-4.14.2
collecting ... collected 15 items

tests/test_ephemeral.py::test_store_has_no_idea_table PASSED               [  6%]
tests/test_ephemeral.py::test_workspace_wiped_after_sweep PASSED         [ 13%]
tests/test_ephemeral.py::test_store_register_get_sweep_ttl PASSED        [ 20%]
tests/test_ephemeral.py::test_store_keeps_fresh_run PASSED               [ 26%]
tests/test_ephemeral.py::test_ack_removes_entry PASSED                   [ 33%]
tests/test_ephemeral.py::test_ack_nonexistent_false PASSED               [ 40%]
tests/test_ephemeral.py::test_unknown_run_id_not_enumerable PASSED       [ 46%]
tests/test_ephemeral.py::test_dashboard_sweep_wipes_workspace_and_410s PASSED [ 53%]
tests/test_result_ack.py::test_result_survives_disconnect_reget_200 PASSED [ 60%]
tests/test_result_ack.py::test_result_after_ack_gone_410 PASSED          [ 66%]
tests/test_result_ack.py::test_ack_before_result_not_ready_409 PASSED    [ 73%]
tests/test_result_ack.py::test_ack_unknown_id_404 PASSED                 [ 80%]
tests/test_result_ack.py::test_expired_result_gone_after_sweep PASSED    [ 86%]
tests/test_result_ack.py::test_fresh_result_survives_sweep PASSED        [ 93%]
tests/test_result_ack.py::test_sweep_removes_only_expired PASSED         [100%]

============================== 15 passed in 1.31s ==============================
```

**Test quality audit:**
- All 15 tests are substantive, not stubs
- No always-pass asserts
- Tests verify real behavior (file creation, HTTP status codes, store state)
- Fake clock correctly drives TTL testing
- `test_workspace_wiped_after_sweep` performs forensic grep of workspace tree
- `test_dashboard_sweep_wipes_workspace_and_410s` is end-to-end (creates files, registers, advances TTL, sweeps, verifies 410)

### 2. Full Test Suite ✅

```
$ timeout 300 venv/bin/python -m pytest tests/ -q
........................................................................ [ 36%]
........................................................................ [ 72%]
........................................................                 [100%]
200 passed in 3.28s
```

- Before T5: 185 passed
- After T5: **200 passed** (15 new T5 tests, 0 regressions)
- All existing tests still pass

### 3. Scope Check ✅

**Expected T5 changes:**
- `src/dashboard.py` — T5 integration (STORE, _sweep_once, lifespan, _lookup, api_ack_result, api_get_result)
- `src/ephemeral_store.py` — new T5 module
- `src/inflight_sweeper.py` — new T5 module
- `tests/test_ephemeral.py` — new T5 tests
- `tests/test_result_ack.py` — new T5 tests
- `notes/evidence/T5-worker.md` — worker evidence
- `notes/TASKBOARD.md` — T5 status change (pending → qa-pending)

**Note:** Untracked `notes/evidence/T4-qa-independent.md` is a T4 leftover (not part of T5). Excluded from T5 commit.

### 4. Security Spot-Check ✅

- **No new stored secrets:** grep for `sk-or-`, `AIza`, `secret`, `password`, `token` in new files → CLEAN
- **BYOK enforcement unchanged:** D1 respected, no server-key fallback reintroduced
- **Path traversal guard:** `inflight_sweeper.py::sweep_run_workspace` correctly validates `ws.parent != runs_root` before removing directories
- **No forbidden fallbacks:** `_lookup` properly returns 410 for gone records, 404 for unknown (S3)

### 5. Quality Check ✅

**Error paths:**
- Critical error paths are loud (API errors raise HTTPException, orchestrator failures emit `run_failed`)
- Silent `except Exception: pass` only in cleanup/best-effort contexts:
  - `_emit_plugin` (fire-and-forget callback, documented as such)
  - `_sweep_once` workspace sweep (best-effort disk cleanup)
  - `api_ack_result` workspace sweep (best-effort disk cleanup)
  - `lifespan._tick` (periodic loop, must not crash)
- These are acceptable for cleanup operations, not critical error paths

**Code quality:**
- `EphemeralStore` is clean, well-documented, follows single responsibility
- `sweep_run_workspace` has proper path traversal guard
- `_sweep_once` correctly orders operations (store sweep first, then workspace cleanup)
- `_lookup` properly distinguishes gone (410) from unknown (404)
- `api_ack_result` validates result exists before ACK (409 if not ready)
- `api_get_result` simplified (removed redundant acked check, now handled by `_lookup`)
- Lifespan context manager properly cancels task on shutdown

**Dead code:** None detected

### 6. Verification Points from REWRITE_PLAN.md ✅

**T5 (Part C):**
- `tests/test_ephemeral.py` + `tests/test_result_ack.py` as specified ✅

**S6 (Part A):**
- "after run end + sweep, `grep -r` of idea text over workspace/state dirs → zero matches" ✅
  - `test_workspace_wiped_after_sweep` performs forensic grep
  - `test_dashboard_sweep_wipes_workspace_and_410s` verifies end-to-end

**S7 (Part A):**
- "finish run, drop client, re-GET result → 200; after ACK, result endpoint → 410; after TTL (mocked clock) → 410" ✅
  - `test_result_survives_disconnect_reget_200` — re-GET → 200
  - `test_result_after_ack_gone_410` — ACK → 410
  - `test_expired_result_gone_after_sweep` — TTL → 410
  - `test_ack_before_result_not_ready_409` — ACK before result → 409

**D2 (Part B):**
- "server persists only in-flight run records with TTL; ideas are never written to disk server-side except inside the ephemeral run workspace, wiped at run end + TTL sweep" ✅
  - `test_store_has_no_idea_table` — no idea table
  - `test_workspace_wiped_after_sweep` — workspace wiped
  - `test_dashboard_sweep_wipes_workspace_and_410s` — end-to-end

**D3 (Part B):**
- "server keeps finished result with TTL (24 h) until client ACKs download" ✅
  - `test_result_survives_disconnect_reget_200` — result survives disconnect
  - `test_result_after_ack_gone_410` — ACK wipes result
  - `test_expired_result_gone_after_sweep` — TTL wipes result
  - `test_fresh_result_survives_sweep` — fresh result survives

## Issues Found

**Minor (not FAIL-worthy):**
1. **Untracked T4 evidence file:** `notes/evidence/T4-qa-independent.md` is a T4 leftover. Not T5's responsibility; excluded from T5 commit. Coordinator should clean up or commit separately.
2. **Silent exception handling in cleanup:** Several `except Exception: pass` in cleanup contexts. Acceptable for best-effort operations (workspace sweep, fire-and-forget callbacks). Critical error paths remain loud.

## Verdict

All verification points from REWRITE_PLAN.md are satisfied:
- T5 tests pass (15/15)
- Full suite passes (200/200, +15 new tests, 0 regressions)
- No scope violations (T5 changes only)
- No security issues
- No quality issues (silent exceptions only in cleanup contexts)
- Implementation is clean, well-documented, and correct

**VERDICT: PASS**
