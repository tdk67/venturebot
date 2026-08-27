# T8 E2E — BYOK key UX (key entry, verify-before-run, localStorage-only, never rendered back)

Verification per REWRITE_PLAN Part C T8:
  * wrong key → blocked with an explicit message (verify fails fast, run gated)
  * grep frontend bundle: the key variable is referenced only in the 2 call
    sites that must send it (create-run and verify); it is never rendered to
    the DOM.

Run against the **stub** server (no real LLM key needed) —
`src/dashboard:app` verify endpoint is real and only checks format:
```
VENTUREBOT_NO_AUTH=1 ./venv/bin/uvicorn src.dashboard:app --host 127.0.0.1 --port 8397
```

## Setup / DOM contract (added in T8)
- `#btn-key-open` → reveal the key entry form
- `#key-form` — the entry form (hidden until opened)
- `#key-input`  — `type="password"`, placeholder `sk-or-v1-… / AIza…`
- `#btn-key-verify` → calls `/api/byok/verify` then saves to localStorage
- `#key-hint`    — result message (visible on success or failure)
- `#key-status`  — reflects saved/validated state; NEVER contains the key text
- `#btn-key-clear` — removes the stored key
- Run is gated: clicking an idea's ▶ Run with no/only-saved key shows a
  blocking message in `#status-line` and does not open the debate panel.

## Scenario A — wrong key is blocked with an explicit message (fail fast)
1. Fresh profile; open `http://127.0.0.1:8097/app`.
2. Add an idea `A CLI tool that summarizes git diffs into plain English`.
3. Without setting a key, click the idea's **▶ Run** button.
4. Assert the debate panel `#debate-run` stays hidden and `#status-line`
   explains the key is missing (text contains "API key").
5. Open the key form (`#btn-key-open`), paste a **wrong/garbage** key, click
   `#btn-key-verify`.
6. Assert `#key-hint` becomes visible and its message says the key "looks
   invalid" (verify fails → run stays blocked).
7. Assert the stored key did NOT persist: opening localStorage key
   `vb-api-key` is absent (invalid keys are not stored).
8. Pass criterion A: wrong key → explicit blocking message, run cannot start.

## Scenario B — valid key is saved in localStorage only, never rendered back
1. In the same profile, paste a **well-formed** key
   `vb-test-key-not-real-example` into `#key-input`, click `#btn-key-verify`.
2. Assert `#key-hint` shows a "Valid" message.
3. Assert the key is now in `localStorage['vb-api-key']` (persisted).
4. Assert NO element on the page (`#key-input`, `#key-status`, `#key-hint`,
   `body` text) contains the key literal — the saved key is never rendered back.
5. Click the idea's **▶ Run** button → the `#debate-run` panel appears (run is
   allowed because the key was verified this session).
6. Pass criterion B: key persisted to localStorage, never rendered to the DOM,
   and unlocking the run required a successful verify.

## Pass criteria
- A: wrong/missing key → explicit blocking message; run cannot start; invalid key un-stored.
- B: valid key persisted (localStorage) and never rendered back; run starts only after verify.