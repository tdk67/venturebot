# T4 QA — Independent Adversarial Verification

**Date:** 2026-08-27  
**Verifier:** Independent QA pass (second verification)  
**Task:** T4 — Rate limits & caps (S1 + S10)

## Verification Protocol

### 1. Test Execution (Independent Run)

```bash
timeout 120 venv/bin/python -m pytest tests/test_rate_limits.py -v
```

**Result:** 8 passed in 0.55s ✅

All 8 tests pass:
- `test_second_concurrent_run_same_ip_429` — 2nd concurrent run returns 429
- `test_concurrent_runs_different_ip_ok` — different IPs can run concurrently
- `test_queued_runs_do_not_block_concurrency` — queued runs don't block
- `test_21st_run_in_hour_same_ip_429` — 21st run in hour returns 429
- `test_hourly_limit_resets_after_window` — hourly limit resets after window
- `test_oversized_body_413` — oversized body returns 413
- `test_4th_sse_conn_same_ip_429` — 4th SSE connection returns 429
- `test_4th_sse_conn_endpoint_429` — SSE endpoint raises 429

```bash
timeout 300 venv/bin/python -m pytest tests/ -q
```

**Result:** 185 passed in 4.27s ✅ (177 prior + 8 new T4 tests)

### 2. Verification Points (REWRITE_PLAN.md Part A)

#### S1 — Open compute endpoint

**Requirement:** "per-IP limits: max 1 concurrent run, 20 runs/hour, request body ≤ 32 KB"

**Implementation:**
- `MAX_ACTIVE_RUNS_PER_IP = 1` ✅
- `MAX_RUNS_PER_HOUR_PER_IP = 20` ✅
- `MAX_BODY_BYTES = 32 * 1024` ✅

**Tests:**
- `test_second_concurrent_run_same_ip_429`: Creates run, marks as executing via `begin_concurrent()`, attempts 2nd create from same IP → 429 ✅
- `test_21st_run_in_hour_same_ip_429`: Creates 20 runs (all return 201), 21st returns 429 ✅
- `test_oversized_body_413`: Sends 33KB body → 413 ✅

**Verdict:** S1 requirements met ✅

#### S10 — SSE fd exhaustion

**Requirement:** "Per-IP SSE connection cap (3)"

**Implementation:**
- `MAX_SSE_PER_IP = 3` ✅
- `sse_acquire()` returns token or None ✅
- `sse_release()` frees token ✅

**Tests:**
- `test_4th_sse_conn_same_ip_429`: Acquires 3 tokens, 4th returns None ✅
- `test_4th_sse_conn_endpoint_429`: Pre-fills 3 tokens, calls endpoint → raises HTTPException(429) ✅

**Verdict:** S10 requirements met ✅

### 3. Code Quality Review

#### src/rate_limit.py

**Thread Safety:**
- All mutable state protected by `_lock` (threading.Lock) ✅
- `client_ip()`, `check_hourly()`, `accept_run_created()`, `has_active_run()`, `begin_concurrent()`, `end_concurrent()`, `sse_acquire()`, `sse_release()`, `clear_all()` all use `with _lock:` ✅

**Error Handling:**
- No bare `except:` clauses ✅
- `client_ip()` has two `except Exception: pass` blocks, but these are acceptable fallbacks for test doubles that don't have headers/client attributes, with final default return of `"testclient"` ✅
- No silent failures in rate limiting logic ✅

**Design:**
- Clean separation: `has_active_run()` (check-only) vs `begin_concurrent()` (reserve slot) ✅
- Executor seam (`begin_concurrent`/`end_concurrent`) properly separated for T5 integration ✅
- Injectable clock (`_clock`) for deterministic testing ✅
- `clear_all()` for test isolation ✅

**Dead Code:** None found ✅

#### src/dashboard.py (T4 changes)

**Integration:**
- Imports rate_limit functions correctly ✅
- `_read_body_limited()` enforces body size limit before parsing ✅
- `api_create_debate()` checks `has_active_run()` → 429, then `check_hourly()` → 429, then `accept_run_created()` ✅
- `api_debate_events()` acquires SSE token → 429 if None, releases in `finally` block ✅

**Error Handling:**
- All rate limit violations raise `HTTPException(429)` with clear messages ✅
- Body size violation raises `HTTPException(413)` ✅
- SSE token released in `finally` to prevent leaks ✅

**Dead Code:** None found ✅

#### tests/test_rate_limits.py

**Test Quality:**
- No stubs or always-pass assertions ✅
- Tests use realistic scenarios with proper setup ✅
- `autouse=True` fixture calls `rate_limit.clear_all()` for test isolation ✅
- Injectable clock (`_FakeClock`) for time-based testing ✅
- Tests verify both success and failure paths ✅

**Coverage:**
- All 4 rate limits tested (concurrent runs, hourly, body size, SSE) ✅
- Edge cases tested (different IPs, queued vs executing, window reset) ✅
- Both module-level and endpoint-level SSE tests ✅

### 4. Scope Check

```bash
git show 58aac99 --stat
```

**Files changed:**
- `notes/TASKBOARD.md` (1 line)
- `notes/evidence/T4-qa.md` (69 lines, new)
- `notes/evidence/T4-worker.md` (119 lines, new)
- `src/dashboard.py` (+70 lines, T4 integration)
- `src/rate_limit.py` (157 lines, new)
- `tests/test_rate_limits.py` (189 lines, new)

**Verdict:** All changes are T4-related ✅

### 5. Security Spot-Check

**Hardcoded Secrets:**
- Test fixtures use `"sk-or-v1-test"` — clearly fake, matches OpenRouter pattern validation ✅
- No real API keys found ✅

**Error Handling:**
- No bare `except:` clauses ✅
- All exceptions are specific (`HTTPException`, `Exception` in fallback contexts) ✅

**Syntax:**
- Both files pass `ast.parse()` validation ✅

**Verdict:** No security issues found ✅

### 6. Design Decision Compliance

**Concurrency Model:**
- Worker's design note: "concurrency cap is enforced at the point of execution" ✅
- `has_active_run()` checks without reserving (safe for T1 skeleton tests) ✅
- `begin_concurrent()`/`end_concurrent()` reserve/release for actual execution (T5 seam) ✅
- This allows T1 contract tests to create multiple queued runs without tripping the limit ✅

**Hourly Window:**
- Rolling window with pruning (`while q and _now() - q[0] > RUN_WINDOW_SECONDS: q.popleft()`) ✅
- Consumed at create time (`accept_run_created()`) ✅
- Injectable clock for deterministic testing ✅

**Body Size:**
- Enforced before JSON parsing (`_read_body_limited()`) ✅
- 32 KB limit matches S1 requirement ✅

**SSE Cap:**
- Token-based acquisition/release ✅
- Released in `finally` block to prevent leaks ✅
- Both module-level and endpoint-level tests ✅

## Conclusion

**VERDICT: PASS** ✅

All verification points from REWRITE_PLAN.md Part A (S1 + S10) are correctly implemented and tested:
- 2nd concurrent run from same IP → 429 ✅
- 21st run in an hour → 429 ✅
- Oversized body → 413 ✅
- 4th SSE conn from same IP → 429 ✅

**Test Quality:** Excellent — no stubs, realistic scenarios, proper isolation, deterministic time testing ✅

**Implementation Quality:** Excellent — thread-safe, clean design, loud error paths, no dead code ✅

**Security:** No issues found ✅

**Scope:** All changes are T4-related ✅

**Full Test Suite:** 185 tests pass (177 prior + 8 new T4 tests) ✅

**Independent Verification Confirms:** Previous QA (T4-qa.md) was correct. T4 is ready for production use.

---

**Recommendation:** T4 implementation meets all requirements. No remediation needed. Task can be marked complete.
