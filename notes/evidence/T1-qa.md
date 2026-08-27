# T1 QA Verdict — API contract skeleton + delete legacy/admin routes

**Task:** T1 (REWRITE_PLAN.md Part C, Phase 1)
**QA:** adversarial re-verification (worker's claim treated as untrusted, re-run fresh)
**Date:** 2026-08-27

## 1. Full suite (Definition of Done #2) — fresh run

```
$ timeout 300 venv/bin/python -m pytest tests/ -q
........................................................................ [ 42%]
........................................................................ [ 85%]
........................                                                 [100%]
168 passed in 3.12s
```
GREEN. No hang (wrapped in `timeout 300`).

## 2. Verification points — each re-run and read for stub/always-pass risk

| VP | Command | Result | Notes |
|----|---------|--------|-------|
| every new route exercised | `pytest tests/test_api_contract.py -v` | 40 passed | Test code read: real assertions (status codes, UUIDv4 `version==4`, body content). No stubs, no `assert True`. |
| legacy routes → 404 (S5) | `pytest "tests/test_api_contract.py::test_legacy_routes_are_gone" -v` | 25 paths × (GET+POST) → 404 PASSED | Parametrized over `/api/budget/raise`, `/api/reset`, `/api/stop`, `/scheduler/dream-review`, `/api/usage`, `/api/auth/*` (5), `/api/ideas*` (6), run/control/steering (11). |
| blank/absent api_key → 400 | `test_create_debate_requires_api_key` | PASSED | Asserts `"api_key" in detail.lower()`. |
| unknown UUID → 404 (S3) | `test_status_unknown_id_404` | PASSED | Uses `uuid.uuid4()`. |
| no list/enumeration endpoint | `test_no_list_endpoint` | PASSED | `set - set` diff against exact 8-route allowlist. |
| OpenAPI snapshot committed | `test_openapi_snapshot_reproducible` | PASSED | Asserts live schema == committed `tests/openapi_snapshot.json`; no legacy paths; required paths present. |

## 3. Scope check (`git status --short`)

```
 M notes/JOURNAL.md         (coordinator "T1 unblock" journal — legit bookkeeping)
 M notes/REWRITE_PLAN.md    (D2/D3/D5/D6 → LOCKED wording; S3/S5/T3 wording)
 M notes/TASKBOARD.md       (D2/D3/D5/D6 locked; T1 → qa-pending)
 M src/dashboard.py         (rewrite: -1245 lines, in-memory registry + 8-route contract)
 M tests/conftest.py        (+31: --regenerate-openapi flag fixture)
 D tests/test_auth_flow.py  (dropped — tested deleted auth routes)
 D tests/test_idea_runs.py  (dropped — tested deleted idea-store routes)
?? tests/test_api_contract.py
?? tests/openapi_snapshot.json
?? notes/evidence/T1-worker.md
```
All changes belong to T1 (or to the already-locked decision bookkeeping).
No `.env`, no stray files.

## 4. Security spot-check

- **D1 / no fallback:** `src/dashboard.py` contains NO server-key env read, no
  `GOOGLE_API_KEY`/`OPENROUTER` fallback. `_require_api_key` raises 400 on
  blank/absent key. Confirmed via grep.
- **S3 / no enumeration:** route table = exactly the 8 contract paths (verified
  live: `sorted(paths)`). No list endpoint.
- **S5 / admin deleted:** no `include_router`/scheduler/oauth wiring in
  `dashboard.py`; entrypoint `Containerfile` = `uvicorn src.dashboard:app`
  (legacy `src/scheduler.py`/`src/oauth.py` are orphaned, not imported).
- **S2 / key redaction:** `api_key` is stored on `RunRecord` in memory only,
  never emitted via `_emit` (payloads are `idea_len`, `urls`, `answer_len`,
  `run_id`), never returned in any response body.
- **S9 / CSP:** strict `script-src 'self'` CSP + nosniff/DENY headers present.
- **secret scan:** `bash scripts/secret_scan.sh --files src/dashboard.py tests/test_api_contract.py tests/conftest.py tests/openapi_snapshot.json` → `secret-scan: CLEAN`.

## 5. Quality

- No `except: pass` (silent swallow) in new code.
- No `@pytest.mark.skip` / `xfail`; single `parametrize` only.
- No `assert True/False/0/1` trivial asserts.
- Error paths are loud: 400 (missing key/idea/answer), 404 (unknown id), 202
  (not ready), 409 (ack before ready), 410 (acked/gone).

## Observations (non-blocking nits, deferred to later tasks)

1. `api_get_result` line 181 has a redundant clause
   `(rec.status == "done" and rec.result is None and rec.acked)` — a strict
   subset of the leading `rec.acked`. Harmless; the whole store is replaced in
   T5 anyway.
2. Remaining legacy *unit* test files (`test_sessions.py`, `test_ideas_store.py`,
   `test_memory.py`, `test_review_fork.py`, `test_checkpoint.py`) still pass
   because their target modules still exist. They are on the plan's DROP list,
   but their removal belongs to T5/T10 (when those modules are deleted/ported),
   not T1 (which is route-contract scope). Not a T1 violation.
3. `/api/byok/verify` is format-only (no outbound call) — correct for the T1
   skeleton; network verification arrives in T3.

VERDICT: PASS
