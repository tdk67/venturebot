# T6 QA Verification — App shell + IndexedDB idea store

**Task:** T6 — App shell + idea store (IndexedDB CRUD, JSON export/import, duplicate-check)
**QA Agent:** adversarial verifier, independent re-check
**Date:** 2026-08-27

## Verification 1 — Full test suite

```
$ timeout 300 venv/bin/python -m pytest tests/ -q
200 passed in 3.64s
```

✅ 200 tests pass, zero regressions from T5 baseline.

## Verification 2 — Security headers tests

```
$ venv/bin/python -m pytest tests/test_security_headers.py -v
tests/test_security_headers.py::test_security_headers_on_html PASSED
tests/test_security_headers.py::test_no_thirdparty_scripts_in_template PASSED
tests/test_security_headers.py::test_app_js_served_and_vendor_files_pinned PASSED
tests/test_security_headers.py::test_inline_script_block_removed_from_template PASSED
4 passed
```

✅ All 4 security header tests pass with the new template.

## Verification 3 — Security spot-checks

| Check | Command / Method | Result |
|-------|-----------------|--------|
| No CDN references | `grep -rn "cdn\.\|unpkg\|jsdelivr\|cloudflare" templates/ static/` | Zero matches in served content (vendored tailwind file contains "cdn.tailwindcss.com" as a warning string only; it is no longer loaded by the template) |
| No eval() in bundle | `grep -n "eval(" static/app.js` | Zero matches |
| innerHTML usage safe | `grep -n "innerHTML" static/app.js` | Only `t.innerHTML=""` (clearing list container before re-render). All user content set via `textContent` through the `dom()` function — XSS-safe |
| No stored secrets | Reviewed all frontend source files | No keys, tokens, or secrets in any frontend file |
| No server-key fallback (D1) | `grep -rn "GOOGLE_API_KEY\|server.key\|fallback" frontend/src/` | Zero matches; frontend has no server key reference |

✅ All security checks pass.

## Verification 4 — TypeScript strict compile

```
$ cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.json
TSC_PASS
```

✅ Strict TypeScript compilation passes with zero errors.

## Verification 5 — esbuild build reproducibility

```
$ cd frontend && npm run build
  ../static/app.js  5.0kb
⚡ Done in 19ms
```

✅ Build produces 5.0kb minified bundle, matches worker's claim.

## Verification 6 — E2E checklist (tests/e2e/ideas.spec.md)

Server: `VENTUREBOT_NO_AUTH=1 venv/bin/uvicorn src.dashboard:app --host 127.0.0.1 --port 8399`
Browser: agent-browser with `--executable-path /root/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome`, sessionMode=fresh.

### Step 1 — Create two ideas
- Filled `#idea-input` with "A CLI tool that summarizes git diffs into plain English", clicked `#btn-add`.
- Filled with "An app that helps gardeners track watering schedules", clicked `#btn-add`.
- Eval result:
  ```json
  {"ideaCount":"2 ideas","items":["An app that helps gardeners track watering schedules","A CLI tool that summarizes git diffs into plain English"],"statusLine":"📝 Saved \"An app that helps gardeners track watering schedules\""}
  ```
  ✅ 2 ideas present, count = "2 ideas".

### Step 2 — Reload durability
- Re-opened page (same session, same IndexedDB).
- Eval result:
  ```json
  {"ideaCount":"2 ideas","items":["An app that helps gardeners track watering schedules","A CLI tool that summarizes git diffs into plain English"],"duplicateHint":"","duplicateVisible":false}
  ```
  ✅ Both ideas persist after reload.

### Step 3 — Duplicate submit warns
- Entered exact text of idea #1 ("A CLI tool that summarizes git diffs into plain English"), clicked `#btn-add`.
- Eval result:
  ```json
  {"ideaCount":"2 ideas","dupHintVisible":true,"dupHintText":"Duplicate: an idea with this exact title (\"A CLI tool that summarizes git diffs into plain English\") already exists."}
  ```
  ✅ Duplicate warning shown; count stays at 2 (no duplicate inserted).

### Step 4 — Export JSON
- Clicked `#btn-export`. File downloaded to `~/Downloads/venturebot-ideas-2026-08-27 (2).json`.
- File contents:
  ```json
  {
    "format": "venturebot-ideas",
    "version": 1,
    "exportedAt": "2026-08-27T16:05:27.633Z",
    "count": 2,
    "ideas": [
      {"id": "9d57e69c-...","title": "An app that helps gardeners track watering schedules","createdAt": ..., "updatedAt": ...},
      {"id": "ab762dbb-...","title": "A CLI tool that summarizes git diffs into plain English","createdAt": ..., "updatedAt": ...}
    ]
  }
  ```
  ✅ Valid JSON with correct format, count=2, both ideas with all required fields.

### Step 5 — Import restores in clean profile
- Opened fresh browser session (clean IndexedDB).
- Verified empty state: `{"ideaCount":"0 ideas","itemCount":0}`.
- Uploaded exported JSON file via `#import-file`.
- Eval result:
  ```json
  {"ideaCount":"2 ideas","items":["An app that helps gardeners track watering schedules","A CLI tool that summarizes git diffs into plain English"],"statusLine":"Import: 2 added, 0 already present (skipped)."}
  ```
  ✅ Both ideas restored from JSON backup in a clean profile.

### Pass criteria (all met)
- A: both ideas survive a reload — **PASS** ✅
- B: duplicate submit warns (client-side) — **PASS** ✅
- C: exported JSON re-imported in a clean profile restores both — **PASS** ✅

## Verification 7 — Scope check

```
$ git diff HEAD --stat
 .gitignore           |    5 +
 notes/TASKBOARD.md   |    2 +-
 static/app.js        | 1385 +-------------------------------------------------
 templates/index.html |  313 ++----------
 4 files changed, 41 insertions(+), 1664 deletions(-)
```

All changes belong to T6:
- `.gitignore`: added `node_modules/`, `frontend/node_modules/`, `frontend/dist/` — T6 build artifacts ✅
- `notes/TASKBOARD.md`: T6 status update ✅
- `static/app.js`: replaced legacy bundle with T6 esbuild bundle ✅
- `templates/index.html`: replaced legacy 300-line shell with T6 app shell ✅
- `frontend/` (untracked): TypeScript source for T6 frontend ✅
- `tests/e2e/` (untracked): T6 E2E spec ✅
- `notes/evidence/T6-worker.md` (untracked): worker evidence ✅

Note: `notes/evidence/T4-qa-independent.md` is an untracked leftover from T4 QA — not T6 scope, not a concern.

No server-side Python changes (consistent with T6 being a frontend-only task).

## Verification 8 — Code quality

- TypeScript source: 6 files (idb.ts, store.ts, app-shell.ts, app.ts, dom.ts, api.ts) — clean separation of concerns ✅
- Strict mode: `noUnusedLocals`, `noUnusedParameters`, `noFallthroughCasesInSwitch` ✅
- XSS prevention: all user content rendered via `textContent` (never `innerHTML` with user data) ✅
- No dead code observed ✅
- No silent error swallowing (`catch` blocks report to `#status-line`) ✅
- `api.ts` contains only type stubs for future tasks (T7-T9) — no premature implementation ✅

## VERDICT

All verification points from REWRITE_PLAN.md Part C T6 are satisfied:
- E2E checklist (ideas.spec.md): 3/3 pass criteria met (A, B, C)
- Full test suite: 200 passed, zero regressions
- Security: no CDN, no eval, no stored secrets, XSS-safe rendering
- Build: TypeScript strict compile + esbuild both pass
- Scope: all changes belong to T6

VERDICT: PASS
