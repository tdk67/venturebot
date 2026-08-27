# T8 worker evidence — BYOK key UX

Task per `notes/REWRITE_PLAN.md` Part C T8:
> **BYOK key UX.** Key entry, `/api/byok/verify` before a first run, stored in
> localStorage, never rendered back, never sent except to create-run/verify.
> Verification: E2E — wrong key → blocked with message; grep frontend bundle:
> key variable referenced only in 2 call sites.

## Files changed (all in T8 scope)
- `frontend/src/byok.ts` (NEW) — key entry + verify + localStorage, isolated module.
- `frontend/src/app.ts` — composition root wires key UX; gates the Run button;
  passes the stored key to `debate.start`; removed the old `sk-or-v1-demo-placeholder`
  fallback (which skipped verification). The stored key is never rendered to DOM.
- `frontend/src/api.ts` — added `verifyByokKey(path)` wrapping `_post(API.byokVerify, { api_key })`.
- `templates/index.html` — added `#key-card` key-entry section:
  `#btn-key-open`, `#key-form`, `#key-input` (`type="password"`), `#btn-key-verify`,
  `#key-hint`, `#key-status`, `#btn-key-clear`.
- `tests/e2e/byok.spec.md` (NEW) — E2E spec (verification point).
- `static/app.js` — regenerated esbuild bundle (D6: plain TS → static bundle).

## Security / design notes
- Key is stored in `localStorage['vb-api-key']`. Never rendered back: the input is
  cleared after verify; status/hint text never include the key literal.
- Key is only ever sent to two endpoints: `POST /api/byok/verify` and
  `POST /api/debates` (create-run). Confirmed below.
- Run is gated: with no key, or a saved-but-unverified key, the Run button shows a
  blocking message in `#status-line` and does not open the debate panel. Verified.
- Invalid keys are NOT stored (verify-fail clears the stored key). Verified.
- Removed the pre-existing `sk-or-v1-demo-placeholder` fallback so a stored/key
  path can never skip real verification (residual fake-key hazard from T7).

## Verification 1 — E2E: wrong key → blocked with message
Server: `VENTUREBOT_NO_AUTH=1 ./venv/bin/uvicorn src.dashboard:app --port 8397`
Browser: agent-browser, `--executable-path /root/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome`,
`sessionMode: fresh` (clean profile). App page `http://127.0.0.1:8397/app`.

### A1 — Run with NO key → blocked
```
{ "debateVisible": false,
  "status": "Set your model API key first (via the key button above)." }
```

### A2 — Wrong key → verify fails, not stored, run blocked
```
{ "hintVisible": true,
  "hintText": "That key looks invalid. It is used only for this debate — double-check it and try again.",
  "storedKey": null,      <- invalid key NOT persisted
  "inputCleared": true }  <- key never echoes back
```

### B1 — Valid key → saved in localStorage, never rendered back
```
{ "hintText": "Valid openrouter key — saved in this browser only.",
  "storedMatches": true,
  "renderedBack": false,    <- key literal NOT in document.body.innerText
  "inputValueAfter": "",
  "keyStatus": "API key saved in this browser only." }
```

### B2 — With verified key, Run proceeds → debate panel opens
```
{ "debateVisible": true,
  "stateLabel": "live" }
```

### Clear key
```
{ "storedAfterClear": null, "status": "No key set yet. Open the form to add one." }
```

All E2E scenarios pass (A1, A2, B1, B2, clear-key).

## Verification 2 — grep frontend bundle: key referenced in only 2 call sites
```
$ grep -o "api_key" static/app.js | wc -l
2
```
Confirmed exactly 2 `api_key` payload sends in the minified bundle — the verify
endpoint and the create-debate endpoint. `storedKey` / `vb-api-key` appear once
each (single reader path that hands the key to `debate.start`). The stored key is
never placed in any DOM node (verified live: `renderedBack: false`).

## TypeScript strict compile
```
$ npx tsc --noEmit        # clean, no output
$ npm run build           # ../static/app.js  12.6kb  ⚡ Done
```

## Full test suite
```
$ timeout 300 venv/bin/python -m pytest tests/ -q
200 passed in 4.91s
```

No git commit/push performed (QA gates commits).