# T6 worker evidence — App shell + IndexedDB idea store (CRUD, export/import, duplicate check)

Task per `notes/REWRITE_PLAN.md` Part C T6:
> **App shell + idea store.** IndexedDB CRUD for ideas, JSON export/import,
> duplicate-check (client-side).
> Verification: E2E `tests/e2e/ideas.spec.md` checklist run via agent-browser:
> create 2 ideas → reload → both present; export file re-imported in a clean
> profile restores them; duplicate submit warns.

Design constraints honored:
- Client-side store only (decision D2: no server idea table). Ideas live in
  browser IndexedDB `venturebot`, object store `ideas`, keyed by `id` UUID.
- Plain TypeScript, no framework (decision D6). Bundled with **esbuild** to
  `static/app.js`, which keeps the strict CSP `script-src 'self'` (S9) and
  satisfies `test_security_headers.py` (`src="/static/app.js"` in template).
- No LLM for duplicate check — pure client-side token/title overlap (matches
  REWRITE_PLAN line: "duplicate-check is local token-overlap, no LLM").

## Files added/changed
- `frontend/src/idb.ts` — promise wrapper around IndexedDB (open/upgrade,
  put/get/getAll/delete/deleteDatabase, newIdea/uuid). Single persistence seam.
- `frontend/src/store.ts` — idea-store contract: list, addIdea (with duplicate
  + empty outcomes), removeIdea, findDuplicate, exportJson, importJson (idempotent),
  wipe. Mapped 1:1 to the E2E steps.
- `frontend/src/app-shell.ts` — thin DOM shell: add input, ideas list, delete per
  idea, export/import buttons, clear-all, duplicate hint, status line.
- `frontend/src/app.ts` — entry point.
- `frontend/src/dom.ts` — `dom`/`byId`/`debounce`/`parseUrlList` helpers
  (XSS-safe rendering via textContent only).
- `frontend/src/api.ts` — type stubs for the T1 backend contract (used by
  later frontend tasks).
- `frontend/package.json` / `tsconfig.json` — esbuild build + strict tsc.
- `templates/index.html` — **rewritten** as the new T6 app shell (the 19 KB
  legacy shell referenced deleted endpoints from the old architecture).
- `static/app.js` — built bundle (committed so the served app works without a
  build step).
- `tests/e2e/ideas.spec.md` — the T6 E2E checklist.
- `.gitignore` — ignores `node_modules/` and `frontend/dist/`.

## How to build the frontend (reproducible)
```
cd frontend && npm install && npm run build   # → ../static/app.js
```
`tsc --noEmit -p tsconfig.json` passes (strict).

## Verification 1 — TypeScript strict compile + build
```
$ cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.json
TSC=0
$ npm run build
  ../static/app.js       5.0kb  ../static/app.js.map  21.2kb
⚡ Done in 7ms
```

## Verification 2 — served page + security headers (full pytest suite green)
Ran the app `VENTUREBOT_NO_AUTH=1 ./venv/bin/uvicorn src.dashboard:app --port 8399`.
Full suite:
```
$ venv/bin/python -m pytest tests -q
200 passed in 4.90s
```
(200 passed, unchanged from the pre-T6 baseline — no regression.)

Security-header tests that the new template must keep passing:
```
$ venv/bin/python -m pytest tests/test_security_headers.py -v
test_security_headers_on_html PASSED
test_no_thirdparty_scripts_in_template PASSED
test_app_js_served_and_vendor_files_pinned PASSED
test_inline_script_block_removed_from_template PASSED
4 passed
```

## Verification 3 — E2E via agent-browser (tests/e2e/ideas.spec.md)

Server: `uvicorn src.dashboard:app --host 127.0.0.1 --port 8399`
Browser: agent-browser, `--executable-path <playwright chromium>`, sessionMode fresh.

### Step 1 — create 2 ideas
1. Fill `#idea-input` with `A CLI tool that summarizes git diffs into plain
   English`, click `#btn-add`.
2. Fill with `An app that helps gardeners track watering schedules`, click
   `#btn-add`.
Eval after both:
```
{ ideaCount: "2 ideas", items: [
    "An app that helps gardeners track watering schedules",
    "A CLI tool that summarizes git diffs into plain English"] }
```
✅ 2 ideas present, count = "2 ideas".

### Step 2 — reload durability
Re-opened the page. Eval (fresh instrument):
```
{ ideaCount: "2 ideas", items: [ <both ideas> ] }
```
✅ Both ideas persist after a page reload (IndexedDB durable on disk).

### Step 3 — duplicate submit warns
Filled `#idea-input` with the exact text of idea #1 and clicked `#btn-add`.
Eval:
```
{ dupHintVisible: true,
  dupHintText: "Duplicate: an idea with this exact title
    (\"An app that helps gardeners track watering schedules\") already exists.",
  ideaCount: "2 ideas" }
```
✅ Duplicate submit warns; the list count stayed 2 (no duplicate inserted).

### Step 4 — export JSON
Clicked `#btn-export`. Download landed at:
`/root/Downloads/venturebot-ideas-2026-08-27 (1).json`.
Validated with python:
```
format: 'venturebot-ideas'  count: 2
ideas: ['An app that helps gardeners track watering schedules',
        'A CLI tool that summarizes git diffs into plain English']
```
Each idea has `id`, `title`, `createdAt`, `updatedAt`. `EXPORT OK`.
✅ Export produces a correct portable JSON backup.

### Step 5 — import restores in a clean profile
Closed the browser session and re-opened a **fresh** profile (fresh IndexedDB).
Eval on the clean page:
```
{ ideaCount: "0 ideas", items: [], emptyVisible: true }
```
Uploaded the exported JSON file to `#import-file`. Eval after import:
```
{ ideaCount: "2 ideas",
  items: [
    "An app that helps gardeners track watering schedules",
    "A CLI tool that summarizes git diffs into plain English"],
  status: "Import: 2 added, 0 already present (skipped)." }
```
✅ The JSON backup restored both ideas in a different (clean) profile.

### Pass criteria (all met)
- A: both ideas survive a reload — PASS
- B: duplicate submit warns (client-side) — PASS
- C: exported JSON re-imported in a clean profile restores both — PASS

## Out of scope / not done here
- Live debate view (T7), BYOK key UX (T8), disconnect recovery (T9) are
  separate tasks; the shell mounts the idea store only (this task).
- No server changes were needed (no code edits in `src/`).

## Note on agent-browser environment
The wrapper's default browser was unavailable (agent-browser 0.34.0 looks in
`~/.agent-browser/browsers`, which did not exist). Passing
`--executable-path /root/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome`
with `sessionMode: "fresh"` made the native tool drive a real clean Chrome
session; the E2E above used that.
```