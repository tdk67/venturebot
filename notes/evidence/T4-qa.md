# T4 — Rate limits & caps — QA verdict

**QA agent:** adversarial verifier
**Date:** 2026-08-27
**Task:** REWRITE_PLAN.md Part C, T4 (S1 + S10)

## Independent verification

### 1. T4 rate limit tests (fresh run)
```
tests/test_rate_limits.py::test_second_concurrent_run_same_ip_429 PASSED
tests/test_rate_limits.py::test_concurrent_runs_different_ip_ok PASSED
tests/test_rate_limits.py::test_queued_runs_do_not_block_concurrency PASSED
tests/test_rate_limits.py::test_21st_run_in_hour_same_ip_429 PASSED
tests/test_rate_limits.py::test_hourly_limit_resets_after_window PASSED
tests/test_rate_limits.py::test_oversized_body_413 PASSED
tests/test_rate_limits.py::test_4th_sse_conn_same_ip_429 PASSED
tests/test_rate_limits.py::test_4th_sse_conn_endpoint_429 PASSED
8 passed in 0.58s
```

### 2. Full test suite (fresh run)
```
185 passed in 3.97s
```
All green. 185 = 177 prior + 8 new T4 tests.

### 3. Verification point cross-reference (REWRITE_PLAN.md)

| Requirement | Test | Status |
|---|---|---|
| 2nd concurrent run from same IP → 429 (S1) | test_second_concurrent_run_same_ip_429 | ✅ |
| 21st run in an hour → 429 (S1) | test_21st_run_in_hour_same_ip_429 | ✅ |
| Oversized body → 413 (S1) | test_oversized_body_413 | ✅ |
| 4th SSE conn from same IP → 429 (S10) | test_4th_sse_conn_same_ip_429 + test_4th_sse_conn_endpoint_429 | ✅ |
| Per-IP limits in-memory (no persistence) | Code review of rate_limit.py — all state in dicts, no DB/file writes | ✅ |
| Rolling hour window resets | test_hourly_limit_resets_after_window (mocked clock) | ✅ |
| Different IPs independent | test_concurrent_runs_different_ip_ok | ✅ |
| Queued runs don't hold concurrency slot | test_queued_runs_do_not_block_concurrency | ✅ |

### 4. Scope check
```
Modified:   notes/TASKBOARD.md, src/dashboard.py
Untracked:  notes/evidence/T4-worker.md, src/rate_limit.py, tests/test_rate_limits.py
```
All changes belong to T4. No unrelated modifications. ✅

### 5. Security spot-check
- **D1 (BYOK, no server key):** Not touched by T4 — still enforced in api_create_debate. ✅
- **No new stored secrets:** `rate_limit.py` contains only counters and constants. ✅
- **No forbidden fallbacks:** No server-key fallback introduced. ✅
- **No bare `except:`:** None found in new code. ✅
- **`except Exception: pass` in `client_ip()`:** Legitimate tolerant fallback for test doubles that lack headers/client attributes. Final default returns `"testclient"`. Acceptable. ✅

### 6. Quality check
- No skipped or xfail tests. ✅
- No always-pass assertions (`assert True`, etc.). ✅
- No dead code added. ✅
- Error paths are loud: `HTTPException(429, ...)`, `HTTPException(413, ...)`. ✅
- SSE `finally` block properly releases the token on disconnect. ✅
- Thread-safe: all mutable state protected by `_lock`. ✅

### 7. Design assessment
The concurrency model (check at create, claim at executor seam) is sound:
- `has_active_run(ip)` is a non-consuming check at create time
- `begin_concurrent(ip, run_id)` / `end_concurrent(ip, run_id)` is the executor seam for T5
- This correctly enforces S1 ("max 1 concurrent run") for executing runs without breaking the T1 skeleton's queued-run behavior

VERDICT: PASS
