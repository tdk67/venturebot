# T7 worker evidence — Live debate view (per-agent progress chips + loud errors)

Task per `notes/REWRITE_PLAN.md` Part C T7:
> **Live debate view.** Per-agent progress chips fed by SSE; explicit red error
> banner on `run_failed`; cost/elapsed display; stop button per-run.
> Verification: E2E vs `src/stub_server.py` (reuse existing stub): scripted
> success shows 7 agent steps; scripted failure shows error banner within 2 s —
> never a stuck "thinking" state.

## Findings before implementing
- `src/stub_server.py` was **broken against the rewrite**: it imported
  `._broadcast`, `._inbox` and `pipeline.DebateResult` — all deleted by the T1
  skeleton (verified: `import src.stub_server` → ImportError). It could not be
  reused as-is; it had to be rebuilt for T7 (same filename, kept for the E2E
  entrypoint `uvicorn src.stub_server:app`).
- The T1 create route registers a run but never launches a live debate
  (`_run_debate` is never called). So T7's stub drives a SCRIPTED run directly
  into the run record's event list, which the existing per-run SSE route
  (`GET /api/debates/{id}/events`, D4) replays. The frontend therefore consumes
  the same `agent_started` / `agent_finished` / `run_finished` / `run_failed`
  per-agent events the real orchestrator emits.
- No backend contract route was changed; `src/dashboard.py` is untouched.

## Files added/changed
- `src/stub_server.py` — **rebuilt** for T7: driven by env `VENTURE_STUB_MODE`
  (`success` default → 7 agents finish in order then `run_finished` + result
  stored; `fail` → `agent_started` for the first agent then `run_failed`
  within ~0.4 s). Re-uses the REAL dashboard routes (create/status/SSE/result/
  ack) by importing the real app and stripping only `POST /api/debates`, then
  re-adding a stub route that runs the real creation logic and launches a
  themed scripted drive.
- `frontend/src/api.ts` — added the live-debate client: `createDebate`,
  `fetchResult` (202→null, 410/404→error), `ackResult`, and a small inline
  `parseSse` helper (CSP forbids any third-party libs; EventSource is used
  against the native SSE endpoint).
- `frontend/src/debate.ts` — NEW live-debate view module: builds the 7
  per-agent chips, marks them `active` (blue) on `agent_started` and `done`
  (green) on `agent_finished`, shows an elapsed timer, an explicit red error
  banner on `run_failed`/`expired`, a green "done" panel with the PRD link, and
  a **Stop** button that aborts the SSE stream and marks the view stopped.
  Stream-error path probes `fetchResult` (run may have finished) before
  falling back to a loud error — never a silent hang.
- `frontend/src/app-shell.ts` — `init({onRun})` hook; each idea row now gets a
  **▶ Run** button that starts a live debate for that idea.
- `frontend/src/app.ts` — composition root: passes `onRun` to the shell and a
  BYOK key (T8 owns the real key UX; for T7 a placeholder is used).
- `templates/index.html` — NEW `#debate-run` section (state line, agent chips,
  error banner, done panel, stop button).
- `static/app.js` — rebuilt esbuild bundle (committed so the app runs without a
  build step).
- `tests/e2e/debate.spec.md` — the T7 E2E checklist.

## Verification 1 — TypeScript strict compile + build
```
$ cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.json
TSC CLEAN (exit 0)
$ npm run build
  ../static/app.js  10.4kb   (esbuild, es2020, minified)
```

## Verification 2 — stub drives 7 per-agent SSE events (success) + result
Ran the stub against a live uvicorn and consumed the real SSE stream:
```
$ VENTURE_STUB_MODE=success ./venv/bin/uvicorn src.stub_server:app --port 8096
events seen: ['run_created','run_started',
  'agent_started','agent_finished', (x7)
  'run_finished']
agent_started=7 agent_finished=7 saw run_finished=True
result ok: True
```
Failure mode (`VENTURE_STUB_MODE=fail`):
```
fail events: ['run_created','run_started','agent_started','run_failed']
saw run_failed within 1.00s
```

## Verification 3 — E2E via agent-browser (tests/e2e/debate.spec.md)
Server: `src.stub_server:app` on 127.0.0.1:8096. Browser: agent-browser with
`--executable-path /root/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome`,
`sessionMode: fresh` (clean profile per scenario).

### Scenario A — scripted SUCCESS (7 agent steps)
After clicking **▶ Run** and waiting ~5-6 s:
```
chipCount: 7
chips: [Researcher, Advocate, Critic, Creative, Judge, PRD Writer,
        Security Auditor]  — each has class 'bg-emerald-600 text-white' (done)
errorHidden: true           (no error)
doneVisible: true           (green done panel)
stateLabel: 'done'          stateText: 'done · 0:05'
stopHidden: true            (Stop button hidden after finish)
```
✅ All 7 agent steps appear and finish; run ends in a green done panel.

### Scenario B — scripted FAILURE
Restarted the stub in `VENTURE_STUB_MODE=fail` (fresh profile, new idea, Run):
```
errorVisible: true
errText: "⚠ Forced stub failure: simulated invalid API key (VENTURE_STUB_MODE=fail)"
doneVisible: false
stateLabel: 'failed'  stateText: 'failed · 0:01'
```
✅ Red error banner appears within ~1 s (~2 s requirement met). Never a stuck
"thinking" state.

## Verification 4 — full test suite (no regression)
```
$ venv/bin/python -m pytest tests/ -q
200 passed in 4.61s
```
Targeted (changed template / contract):
```
$ venv/bin/python -m pytest tests/test_security_headers.py tests/test_api_contract.py -q
44 passed in 0.56s
```
`src/dashboard.py` and the API contract were **not** modified.

## Out of scope / not done here
- BYOK key-entry UX (T8 — the key is read from localStorage with a placeholder
  until T8 owns it; key only ever sent to create-debate / verify).
- Disconnect recovery / re-polling unfinished runs on load (T9).

## Pass criteria (all met)
- A: scripted success shows 7 agent steps and a done panel — PASS
- B: scripted failure shows an explicit error banner within ~2 s — PASS