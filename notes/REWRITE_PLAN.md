# VentureBot Rewrite — Actionable Plan with Verification Points

**Date:** 2026-08-27
**Rule:** EVERY task has a verification point. NO code is written for a task
before its verification point exists in this document. A task is DONE only
when its verification passes. Supersedes all `*.md.keep` plans.
**Status of design decisions:** D1, D4 LOCKED by user 2026-08-27; D2, D3, D5,
D6 PROPOSED — confirm/change before Phase 1 starts.

**Execution model:** tasks run via the detached agent workflow in
`~/pi-workflow/` (NOT part of this repo — generic infra): coordinator starts
`run_task.sh ~/venturebot Tn` (worker, fresh context, no commit) then
`run_qa.sh ~/venturebot Tn` (adversarial verifier; only QA commits/pushes on
PASS and writes the JOURNAL entry). Status: `notes/TASKBOARD.md`, monitor:
`~/pi-workflow/board.sh ~/venturebot`.

---

## Part A — Security review of the new approach (no auth + BYOK + client-stored data)

Context: login never worked reliably; decision is to move forward WITHOUT
authentication. Users bring their own LLM API key (BYOK), ideas/history live
in the browser (IndexedDB + export/import), the backend is near-stateless
(only in-flight runs), deployed at **idea-lint.my** (GCP). No auth means: no
identity, no per-user accounts, no server-side user data.

### A1 — Findings and mandatory mitigations

| # | Finding | Severity | Mitigation (mandatory) | Verification |
|---|---------|----------|------------------------|--------------|
| S1 | **Open compute endpoint.** Without auth, `POST` create-run is callable by anyone. With a server key that burns OUR money; also DoS vector. | HIGH | BYOK is REQUIRED in production (no server-key fallback). Plus per-IP limits: max 1 concurrent run, 20 runs/hour, request body ≤ 32 KB, run wall-clock timeout 30 min. | `tests/test_rate_limits.py`: 2nd concurrent run from same IP → 429; 21st run in an hour → 429; oversized body → 413. `tests/test_api_contract.py`: create-run with no key → 400 "api_key required". |
| S2 | **BYOK key handling.** User keys transit the server. | HIGH | Key arrives per-request, held in memory only for that run's lifetime, never written to disk/state/logs, redacted from any header logging. No key reuse across runs. | `tests/test_key_canary.py`: submit run with canary key `vb-canary-<uuid>`; assert canary appears NOWHERE in stdout/stderr capture, workspace dir dump, or any state file after run ends. |
| S3 | **Run enumeration.** No auth → anyone who knows a run ID can read it. | MED | Run IDs = UUIDv4 (122 bits). NO list/enumeration endpoint exists. Status endpoint returns 404 for unknown IDs (never 403). | `tests/test_api_contract.py`: GET status of random UUID → 404; grep route table for list endpoints → none. |
| S4 | **Event cross-talk.** Current `/api/events` broadcasts ALL events to ALL clients (verified in `main`). Public + multi-visitor → strangers see each other's debate. | HIGH | Events are per-run: `GET /api/debates/{id}/events`. Global `_broadcast` removed. | `tests/test_events_isolation.py`: two runs in parallel; client of run A never receives run B's events. |
| S5 | **Admin endpoints open.** Verified in `main` under NO_AUTH: `/api/budget/raise` lets ANYONE raise the spend cap arbitrarily; `/api/reset` wipes state; `/api/stop` kills runs; `/scheduler/dream-review` triggerable. | **CRITICAL** | These endpoints are DELETED in the rewrite (not gated — there's no identity to gate them with). Budget cap fixed by env at startup, not raiseable at runtime. | `tests/test_api_contract.py`: all four → 404. |
| S6 | **Data at rest.** The privacy claims on the landing page must match reality (see CLAIMS_VS_REALITY — prio #1). | MED | Server persists only in-flight run records with TTL; ideas are never written to disk server-side except inside the ephemeral run workspace, wiped at run end + TTL sweep. | `tests/test_ephemeral.py`: after run end + sweep, `grep -r` of idea text over workspace/state dirs → zero matches. |
| S7 | **Disconnect loses the debate** (the open question from the timed-out session). | HIGH | **D3 decision (proposed):** server keeps finished result with TTL (24 h) until client ACKs download. Client stores `run_id` in IndexedDB; on reload it re-polls. Run is NOT resumable mid-flight (v1: restart), but the RESULT is never lost once produced. | `tests/test_result_ack.py`: finish run, drop client, re-GET result → 200; after ACK, result endpoint → 410; after TTL (mocked clock) → 410. |
| S8 | **Prompt injection** via idea text into sub-agents. | MED | Keep `guard.py`/`input_guard.py`; orchestrator sub-agents get no file-write outside per-run workspace (isolation already exists). | Port `tests/test_safety.py` + `test_workspace_isolation.py` — must pass unchanged. |
| S9 | **Client-side key storage.** Key sits in localStorage — XSS would steal it. | MED | Strict CSP `script-src 'self'` + vendored libs (already built in A2, reuse), no third-party scripts, no eval. | `tests/test_security_headers.py` reused; `grep -r "cdn\.\|unpkg\|jsdelivr" templates/ static/` → zero matches. |
| S10 | **SSE fd exhaustion.** Open SSE endpoints attract connection hoarding. | LOW | Per-IP SSE connection cap (3); idle timeout 10 min; keepalive ping 15 s. | `tests/test_rate_limits.py`: 4th SSE conn from same IP → 429. |
| S11 | **Log redaction** | MED | Keep `store.log()` metadata-only redaction (already built). | `tests/test_log_redaction.py` reused, must pass. |

### A2 — LIVE exposure on the VPS right now (venturebot.taskmind-ai.com, NO_AUTH=1)

Verified today on `main`: anyone on the internet can currently
1. `POST /api/budget/raise {"limit": 999999}` and then
2. `POST /api/run-phase1` without a key → burns the SERVER's Gemini key.

**Recommendation R1 (needs your OK):** remove `GOOGLE_API_KEY` from the VPS
`.env` so `run-phase1` only works BYOK — then public access costs us nothing.
Alternatively IP-allowlist the nginx vhost. Until then: accepted risk, capped
by the $20/day default budget *unless raised via S5*. Do not put the VPS URL
in the README (decided).

---

## Part B — Design decisions to lock (before Phase 1 code)

| # | Decision | PROPOSED answer | Verification (how we know it's locked) |
|---|----------|-----------------|----------------------------------------|
| D1 | Where do LLM calls execute? | **LOCKED (2026-08-27):** Backend proxies them with the user's BYOK key. **No stored server key, no fallback anywhere** — server Gemini key removed from VPS; verified nothing else needs it (duplicate-check is local token-overlap, no LLM; memory/dream-review parked per D5). | Decision recorded here; GCP Secret Manager entry to drop at T12. |
| D2 | What does the server persist? | Only run records: `{run_id, status, event log, result}` until ACK/TTL. No idea table, no users, no lessons. | State-contract paragraph in this doc confirmed. |
| D3 | Disconnect behavior | Result held server-side until client ACK (Part A / S7). Mid-flight resume: OUT for v1. | S7 test exists and passes. |
| D4 | Event scoping | **LOCKED (2026-08-27):** global broadcast breaks privacy — deleted. Events per-run: `GET /api/debates/{id}/events`. | S4 test exists and passes. |
| D5 | Self-improvement memory (lessons/dream review) | **PARK for hackathon.** Server-side lessons contradict the no-auth/no-data model. Revisit post-hackathon (client-side lessons). | PARKED_IDEAS.md updated with this entry. |
| D6 | Frontend stack | Plain TypeScript, no framework, esbuild bundle to `static/` (keeps CSP `'self'` trivially). | One-line decision recorded here. |

---

## Part C — Task list

### Phase 1 — Backend core (FastAPI, near-stateless)

| # | Task | Verification (must exist BEFORE code) |
|---|------|----------------------------------------|
| T1 | **API contract skeleton.** Routes: `POST /api/debates` (body: idea, api_key, urls? → 201 `{run_id}`), `GET /api/debates/{id}` (status), `GET /api/debates/{id}/events` (SSE), `GET /api/debates/{id}/result` (200 result / 202 not-ready / 410 gone), `POST /api/debates/{id}/result/ack`, `POST /api/debates/{id}/clarify`, `POST /api/byok/verify`, `GET /api/health`. DELETE all legacy admin/ideas/auth routes. | `tests/test_api_contract.py`: every route above exercised for happy path + unknown-ID 404 + legacy routes (`/api/reset`, `/api/stop`, `/api/budget/raise`, `/scheduler/*`, `/api/ideas`, `/api/auth/*`) → 404. OpenAPI snapshot committed; diff must be reviewed on change. |
| T2 | **Orchestrator hardening.** Wrap the loop so no exception can kill a run silently; emit `agent_started`/`agent_finished` (agent name, model, duration) for every sub-agent and `run_failed` with reason on error. | `tests/test_orchestrator_errors.py`: (a) monkeypatch a sub-agent to raise → run ends `failed`, SSE yields `run_failed` with reason, process alive; (b) happy path emits start+finish event for each of the 7 sub-agents in order. |
| T3 | **BYOK plumbing.** Key per-request → memory only → passed to LLM client → discarded at run end. Redact from all logging. **No server-key fallback exists in any form (D1).** | `tests/test_key_canary.py` (S2) + `tests/test_api_contract.py`: create-run without key → 400, always. |
| T4 | **Rate limits & caps** (S1, S10). | `tests/test_rate_limits.py` as specified in S1/S10. |
| T5 | **Ephemeral store + TTL sweeper + ACK** (S6, S7, D2, D3). | `tests/test_ephemeral.py` + `tests/test_result_ack.py` as specified. |

### Phase 2 — Frontend (TypeScript)

| # | Task | Verification |
|---|------|--------------|
| T6 | **App shell + idea store.** IndexedDB CRUD for ideas, JSON export/import, duplicate-check (client-side). | E2E `tests/e2e/ideas.spec.md` checklist run via agent-browser: create 2 ideas → reload → both present; export file re-imported in a clean profile restores them; duplicate submit warns. |
| T7 | **Live debate view.** Per-agent progress chips fed by SSE; explicit red error banner on `run_failed`; cost/elapsed display; stop button per-run. | E2E vs `src/stub_server.py` (reuse existing stub): scripted success shows 7 agent steps; scripted failure shows error banner within 2 s — never a stuck "thinking" state. |
| T8 | **BYOK key UX.** Key entry, `/api/byok/verify` before first run, stored in localStorage, never rendered back, never sent except to create-run/verify. | E2E: wrong key → blocked with message; grep frontend bundle: key variable referenced only in 2 call sites. |
| T9 | **Disconnect recovery.** Persist `{run_id}` per idea in IndexedDB; on load, re-poll unfinished runs; fetch+ACK result. | E2E: start debate → close tab → reopen → run status recovered; finished result retrievable and marked downloaded. |

### Phase 3 — Quality & hardening

| # | Task | Verification |
|---|------|--------------|
| T10 | **Reuse gates.** Port and keep passing: PRD scanner, artifact scanner, workspace isolation, log redaction, security headers, safety tests. | `pytest tests/` green with the 6 ported files; count of ported tests ≥ 60. |
| T11 | **Landing + privacy wording.** Landing page states exactly: no accounts, your key, ideas stay in your browser, server holds only in-flight debates temporarily. | Checklist cross-referencing every claim line in CLAIMS_VS_REALITY.md → each marked TRUE with pointer to the test proving it. |

### Phase 4 — Deploy (GCP, idea-lint.my)

| # | Task | Verification |
|---|------|--------------|
| T12 | **Cloud Run + Firebase Hosting** for `idea-lint.my`; no LLM keys in env/Secret Manager at all (D1); no OAuth env vars left. | `curl https://idea-lint.my/api/health` → 200; `curl` create-run without key → 400; SSE connection through Firebase stays alive ≥ 60 s (keepalive ping observed). |
| T13 | **Production smoke.** One REAL end-to-end debate with a real BYOK key. | Full debate finishes; PRD downloads; verify in Cloud Run logs that no key material appears (`grep` canary + `sk-or-`/`AIza` patterns → zero). |

---

## Part D — Global acceptance gate (all five must pass before "done")

1. **Happy path:** idea + valid key → live per-agent progress → verdict + PRD downloadable.
2. **Loud failure:** invalid key / mid-run API outage → explicit error banner, no silent hang, run marked failed.
3. **Disconnect:** close browser mid-run → reopen → recover status; if finished, result still downloadable, then ACK wipes it.
4. **Isolation:** two browsers, two runs — zero cross-visible events or data.
5. **No data left:** after runs complete, server disk contains no idea text, no keys, no user content (forensic grep script committed as `tests/forensic_check.sh`).

---

## Test reuse decision (required before Phase 1)

KEEP (port in T10): `test_prd_scanner`, `test_artifact_scanner`,
`test_workspace_isolation`, `test_log_redaction`, `test_security_headers`,
`test_safety`, `test_steering` (if steering survives D-review),
`test_clarify_pause` (adapt to new clarify route).
DROP with the old architecture: auth/session/OAuth tests (`tests/test_auth_flow.py`,
`test_sessions.py`), `test_ideas_store`/`test_idea_runs` (server-side idea store is gone),
`test_memory`/`test_review_fork` (D5 parked), `test_checkpoint` (replaced by T5 design).

## Sequencing

```
D1-D6 locked  →  T1 (contract tests written FIRST, fail)  →  T2-T5 (make them pass)
              →  T6-T9 frontend (against stub server first, then real BE)
              →  T10-T11  →  T12-T13 deploy + smoke  →  Part D gate
```

Est. total: ~3-4 focused days. Anything not in this list is out of scope
until the Part D gate passes.
