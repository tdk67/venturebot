# TASKBOARD — VentureBot rewrite (single source for task status)

**Statuses:** pending | worker-running | qa-pending | done | failed | blocked
**Protocol:** coordinator starts `~/pi-workflow/run_task.sh ~/venturebot Tn` →
worker implements + evidence (NO commit) → coordinator starts
`~/pi-workflow/run_qa.sh ~/venturebot Tn` → QA re-verifies adversarially;
ONLY QA commits+pushes+journals on PASS.
Monitor: `~/pi-workflow/board.sh ~/venturebot`. One worker and one QA at a time.

## Design decisions (locked before any task starts)

| # | Decision | Status |
|---|----------|--------|
| D1 | LLM calls execute on backend with BYOK key; **no stored server key, no fallback anywhere** | LOCKED (2026-08-27, user) |
| D4 | Events per-run (`/api/debates/{id}/events`); global broadcast deleted — privacy requirement | LOCKED (2026-08-27, user) |
| D2 | Server persists only run records until ACK/TTL | LOCKED (2026-08-27, user: proceed) |
| D3 | Result held server-side until client ACK; no mid-flight resume in v1 | LOCKED (2026-08-27, user: proceed) |
| D5 | Self-improvement memory PARKED for hackathon | LOCKED (2026-08-27, user: proceed) |
| D6 | Frontend: plain TypeScript, no framework | LOCKED (2026-08-27, user: proceed) |

## Tasks

| # | Task (see REWRITE_PLAN.md Part C) | Status | Evidence / QA |
|---|-----------------------------------|--------|---------------|
| T1 | API contract skeleton + delete legacy/admin routes | done | f1325a9 (notes/evidence/T1-worker.md, T1-qa.md) |
| T2 | Orchestrator hardening (loud failures, per-agent events) | done | eb7ba4b (notes/evidence/T2-worker.md, T2-qa.md) |
| T3 | BYOK plumbing (memory-only keys, redaction) | done | 3aa4c1b (notes/evidence/T3-worker.md, T3-qa.md) |
| T4 | Rate limits & caps | pending | |
| T5 | Ephemeral store + TTL sweeper + ACK | pending | |
| T6 | Frontend shell + IndexedDB ideas + export/import | pending | |
| T7 | Live debate view + explicit errors | pending | |
| T8 | BYOK key UX | pending | |
| T9 | Disconnect recovery | pending | |
| T10 | Reuse gates (port scanners/guards tests) | pending | |
| T11 | Landing + truthful privacy wording | pending | |
| T12 | Cloud Run + Firebase idea-lint.my deploy | pending | |
| T13 | Production smoke (real BYOK debate) | pending | |

Sequencing: T1 → T2 → T3 → T4 → T5 → (T6,T7,T8,T9) → T10 → T11 → T12 → T13.
Global gate: REWRITE_PLAN.md Part D (5 scenarios) after T13.
