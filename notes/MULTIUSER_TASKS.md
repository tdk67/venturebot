# Multi-User Implementation — Task List

**Date:** 2026-08-23
**Sources:** `notes/MULTI_USER_DESIGN.md`, `notes/MULTIUSER_SECURITY_REVIEW.md` (§9 prioritized fix list)
**Rule:** every P0 item must land with tests before the next one starts.

## Phase A — Security hardening of existing code (carries into multi-user)

| # | Task | Review ref | Status |
|---|------|-----------|--------|
| A1 | **Per-run workspace isolation** — orchestrator file tools scoped to `workspace/runs/{run_id}/`, path-traversal guard, no shared global workspace dir | W4a / P0-1 | ✅ done |
| A2 | **Security headers + CSP + externalize app JS + vendor/self-host CDN libs (pinned)** — HSTS, XCTO, Referrer-Policy, frame-ancestors; strict CSP `script-src 'self'`; move 1.2k-line inline script to `/static/app.js`; self-host marked/dompurify/tailwind | G1–G3, W3, W6 / P0-2 | ✅ done |
| A3 | **Log redaction at source** — `store.log()` and orchestrator prints emit metadata only (agent/model/length), never debate content; state.json keeps UI feed | G4, W7 / P0-3 | ✅ done |
| A4 | **Privacy wording fix** — design doc: stop claiming "in-flight only" breach blast radius while backup key escrow ships; state escrow trade-off plainly | W1 / P0-5 | ✅ done |
| A5 | **Session hardening foundation** — server-side session store (hashed tokens), rotation on login, logout revocation; replaces signed-cookie-only auth | G5–G7 / P0-6 | ✅ done |
| A6 | **OAuth code flow + PKCE + state + nonce** replacing GIS inline flow (BE does exchange; secret stays server-side) | G6 / P0-6 | ⬜ |

## Phase B — Multi-user data plane

| # | Task | Review ref | Status |
|---|------|-----------|--------|
| B1 | `user_id` (google sub) on ideas/runs/checkpoints stores; per-route ownership checks | §4.2 | ⬜ |
| B2 | Per-route ownership matrix as two-user IDOR integration tests; non-owner gets **404, not 403** everywhere | §4.2 / P0-4 | ⬜ |
| B3 | Delete/scope legacy global endpoints: `/api/steering` (global), `/api/reset`, global kill switch, global `_broadcast`, global state file | §4.2 / P0-6 | ⬜ |
| B4 | Erasure endpoint purges usage ledger + revokes Google tokens | L1, W8 / P0-7 | ⬜ |
| B5 | Per-user rate limits, budgets, queue caps (adopt PUBLIC_DEPLOYMENT_DESIGN mechanics) | §0 | ⬜ |
| B6 | Ephemeral debate engine: encrypted debate rows, ACK-before-wipe protocol, TTL sweeper w/ `pulling` state | §2.1, W10 | ⬜ |
| B7 | Lesson-poisoning moderation pass + lesson framing ("observations suggest…", not directives); cap lessons/day | W4b / P1-9 | ⬜ |
| B8 | Drive backup: access-token-only sync, no refresh token in IndexedDB; incremental consent for `drive.appdata` | W9 / P1-10 | ⬜ |

## Phase C — Legal / launch ops (mostly docs)

| # | Task | Ref | Status |
|---|------|-----|--------|
| C1 | VPS location decision + transfer basis documented in privacy policy | L2 / P0-8 | ⬜ |
| C2 | Privacy policy, ToS, first-login consent screen, "AI-generated" labels on exports | L3, L6–L8 / P1-12 | ⬜ |
| C3 | Google OAuth verification application (`drive.appdata` is a sensitive scope) | §3.3 / P1-13 | ⬜ |
| C4 | Key rotation runbook for `K_be`; DPIA-lite; reCAPTCHA v3 if abuse appears | P2 | ⬜ |

---

## Implementation log

### A1 — Per-run workspace isolation (2026-08-23)
- `OrchestratorTools` now receives a per-run workspace root (`workspace/runs/{run_id}/`);
  `_read_workspace_file` / `_write_workspace_file` resolve inside it and reject
  traversal (`..`, absolute paths).
- Legacy flat files under `workspace/` are readable by nothing anymore — each run
  starts clean; artifacts live only in that run's dir.
- Tests: `tests/test_workspace_isolation.py`.

### A2 — CSP + externalized JS + vendored libs (2026-08-23)
- `static/app.js` (extracted from index.html), `static/vendor/{tailwind-3.4.16,marked-12.0.2,purify-3.2.4}.min.js`
  pinned copies → CSP `script-src 'self'` kills third-party CDN compromise (W6) and
  most XSS-to-data-theft chains (W3).
- Headers middleware: HSTS (when COOKIE_SECURE), XCTO, Referrer-Policy,
  X-Frame-Options/frame-ancestors 'none', CSP.
- All 21 inline onclick/onchange/oninput handlers converted to id-based listeners
  + `data-action` delegation in app.js (inline handlers are blocked by strict CSP).
- Print-report popup: inline `<script>window.onload=print()</script>` replaced with
  same-origin opener calling `w.print()` (inline script would be blocked).
- Verified: no eval/new Function in vendored libs; all assets 200 from /static;
  zero third-party script hosts remain in the template.
- ⚠️ PENDING: manual browser smoke test (agent-browser tooling broken in this env) —
  click through: debate start/stop, usage periods, verdict buttons, idea modal close,
  import/export, dream review.

### A3 — Log redaction (2026-08-23)
- `store.log()` prints metadata only (`agent/model/message-length`) to stdout;
  full text still reaches state.json for the dashboard feed.
- Orchestrator stdout prints of idea/transcript content removed.

### A5 — Server-side sessions (2026-08-23)
- `src/sessions.py`: SQLite session store — only sha256 token hashes persisted,
  fresh token per login (rotation/anti-fixation), logout revokes the row,
  30-day sliding expiry, lazy purge at startup.
- `auth.py` rewritten off itsdangerous signed cookies; Google verify now also
  returns `sub` (Phase B primary key).
- Logout endpoint revokes server-side; expired-session purge in lifespan.
- Tests: `tests/test_sessions.py` (7 cases incl. no-raw-token-on-disk).

### A4 — Privacy wording (2026-08-23)
- MULTI_USER_DESIGN.md threat-table row corrected; escrow trade-off stated plainly.
