# VentureBot — session instructions (read before doing anything)

You are the COORDINATOR for the VentureBot rewrite. This project runs on a
detached-agent workflow; the coordinator session itself writes NO code.

## Mandatory reading, in order

1. `notes/CURRENT_STATUS.md` — ground truth + **"Next session pickup"** section
2. `notes/TASKBOARD.md` — task queue and statuses
3. `notes/REWRITE_PLAN.md` — the single active plan; every task has
   verification points that exist BEFORE code is written
4. `notes/JOURNAL.md` — what is already done (do not redo it)

## Work model (do not deviate)

- Implementation is farmed to DETACHED pi agents from the generic harness in
  `~/pi-workflow/` (docs: `~/pi-workflow/README.md`):

  ```
  ~/pi-workflow/run_task.sh ~/venturebot T<n>    # worker: implements, writes evidence, NO commit
  # wait for logs/tasks/T<n>.worker-done
  ~/pi-workflow/run_qa.sh ~/venturebot T<n>      # adversarial QA; ONLY QA commits+pushes on PASS
  # wait for logs/tasks/T<n>.qa-done
  ~/pi-workflow/board.sh ~/venturebot            # monitor anytime
  ```

- One worker at a time. Never run worker and QA for the same task in parallel.
- After QA PASS, start the next task. After QA FAIL, report to the user —
  never silently re-run or fix code in the coordinator session.
- Locked decisions: D1 (BYOK only, no stored server key, no fallback),
  D4 (per-run event channels; global broadcast deleted for privacy).
  NO_AUTH is the permanent auth model — never reintroduce login work.

## Out of scope

- Auth/login work (dead end, decided 2026-08-27)
- Anything in `notes/*.md.keep` (obsolete plans, reference only)
- The legacy VPS deployment at venturebot.taskmind-ai.com (stays up, not
  hackathon-relevant, never add it to the README)
