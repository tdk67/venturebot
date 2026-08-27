# JOURNAL — VentureBot rewrite (append-only memory log)

Every completed task gets one entry here (written by the QA agent after PASS).
Coordinator-level decisions are journaled by the coordinator.

## 2026-08-27 — Setup decisions (coordinator)
- Auth abandoned permanently: VPS flipped to `VENTUREBOT_NO_AUTH=1` (login
  never worked; new approach needs no auth). No further auth work on old app.
- Server Gemini key REMOVED from VPS `.env` (R1): public access now costs us
  nothing; duplicate-check verified LLM-free (local token overlap), so nothing
  else needed the key.
- Locked: D1 (BYOK everywhere, no server-key fallback), D4 (per-run event
  channels; global broadcast = privacy violation, deleted).
- 14 obsolete plans renamed `*.md.keep`; REWRITE_PLAN.md is the single active
  plan with per-task verification points (tests exist before code).
- Workflow established: changes are executed by DETACHED pi worker + QA agents
  from the generic harness in **`~/pi-workflow/`** (run_task.sh / run_qa.sh /
  board.sh — intentionally NOT stored under this repo); coordinator session
  only starts/monitors. QA gates every commit+push and journals here.
