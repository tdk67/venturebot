# T7 E2E — Live debate view (per-agent progress chips + loud errors)

Run via agent-browser against the **stub** debate server (`src/stub_server.py`),
which re-uses the real dashboard routes but drives scripted per-agent runs.
Verification per REWRITE_PLAN Part C T7.

## Setup
- Stub server, **success** mode:
  ```
  VENTURE_STUB_MODE=success ./venv/bin/uvicorn src.stub_server:app --host 127.0.0.1 --port 8096
  ```
- App page: http://127.0.0.1:8096/app
- The live-debate panel ids: `#debate-run`, `#debate-agents` (chips), `#debate-state`,
  `#debate-error`, `#debate-done`, `#btn-stop`.

## Scenario A — scripted SUCCESS shows all 7 agent steps
1. Open the app page in a **fresh** browser profile.
2. Add an idea `A CLI tool that summarizes git diffs into plain English` via
   `#idea-input` + `#btn-add`.
3. Click the idea row's **▶ Run** button.
4. Assert `#debate-run` is visible.
5. Wait for the stream to complete (~5-6 s). Assert:
   - each of the 7 agent chips reached a "done" style: `#debate-agents` contains
     exactly the 7 names Researcher, Advocate, Critic, Creative, Judge,
     PRD Writer, Security Auditor.
   - each chip carries a `done` state: the chip element has the `bg-emerald-600`
     class (set on `agent_finished`).
   - `#debate-state` shows `done` label.
   - `#debate-done` panel is visible.
   - `#debate-error` is hidden (never a stuck "thinking" state).
6. Pass criterion A: all 7 agent steps appear and finish; run ends in a done panel.

## Scenario B — scripted failure shows the red error banner within ~2 s
1. Stop the success stub; start the stub in **fail** mode:
   ```
   VENTURE_STUB_MODE=fail ./venv/bin/uvicorn src.stub_server:app --host 127.0.0.1 --port 8096
   ```
2. In a **fresh** profile, open the app, add any idea, click **▶ Run**.
3. Within ~2 s, assert:
   - `#debate-error` is **visible** (class `hidden` removed) and its text
     mentions the failure.
   - `#debate-state` shows the `failed` label.
   - `#debate-done` stays hidden.
4. Pass criterion B: an explicit red error banner appears quickly — the UI never
   sits stuck in "thinking".

## Pass criteria
- A: scripted success shows 7 agent steps and a done panel.
- B: scripted failure shows an explicit error banner within ~2 s.