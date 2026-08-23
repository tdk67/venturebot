# Multi-User VentureBot — End-to-End Design (Local-First Hybrid)

**Date:** 2026-08-23
**Status:** DESIGN — for review before implementation
**Builds on:** `notes/PUBLIC_DEPLOYMENT_DESIGN.md` (queue/rate-limit/BYOK mechanics), `notes/MULTI_TENANT_DECISION.md` (why now)

---

## 0. Design principles

1. **The user's device is the source of truth.** Ideas, transcripts, PRDs, scores live in the browser (IndexedDB) and are backed up to the user's own cloud storage. The backend is a *compute node*, not a data warehouse.
2. **Backend amnesia.** The BE keeps only what a *currently running debate* needs, encrypted at rest, with a short TTL. A hacked BE leaks ciphertext of in-flight debates — nothing else, nobody else's history.
3. **No silent trust.** The BE may only delete a finished debate's data after the client **acknowledges durable receipt** of the results. Until then, results stay (encrypted) for crash recovery.
4. **Every expensive action is owned:** rate limits, per-user budgets, and BYO API keys make the free shared infrastructure abuse-resistant.
5. **One identity:** Google account. Registration = first login. No passwords of our own.

---

## 1. Prior art (how others solve this)

| Pattern | Used by | Takeaway |
|---|---|---|
| Local-first, cloud optional | Excalidraw, Obsidian, Ink & Switch essays | App is fully usable offline; sync is an enhancement, not a requirement |
| App-private cloud folder | Google Drive `appDataFolder` (hidden per-user folder, `drive.appdata` scope, invisible in Drive UI, counts against user's quota) | Ideal for "backup/restore" without letting us browse the user's files; user revokes = we lose access, their data stays theirs |
| E2EE sync | Bitwarden, Standard Notes, Joplin (encrypts before syncing to Drive/Dropbox) | Encrypt client-side before upload; server stores blobs it cannot read |
| Sync engines | Linear, Figma | Full offline copy + background reconciliation; heavier than we need for v1 |
| BYOK | OpenRouter, many AI tools | Key lives in client, sent per-request, never persisted; document honestly that it transits BE memory |

**Decision:** IndexedDB (primary) + **Google Drive `appDataFolder` backup, client-side encrypted**. Firebase/Firestore rejected for backup because data would be readable by the backend project (and by us) — it reintroduces exactly the custody problem we're removing. (Firebase Auth alone remains an option for identity if plain Google OAuth is inconvenient, but plain OAuth is simpler.)

---

## 2. Architecture overview

```
┌──────────────────────────── BROWSER (owner of data) ───────────────────────────┐
│  IndexedDB "venturebot"                                                        │
│    ideas / idea_runs / transcripts / PRDs / verdicts / settings / BYOK key     │
│    + device key K_dev (random 256-bit, generated at first login)               │
│  Sync worker: debounced push → Drive appDataFolder (E2EE blob, AES-GCM(K_bk))  │
│  On login/new device: pull blob → decrypt → merge → IndexedDB                  │
└───────────────┬────────────────────────────────────────────────────────────────┘
                │  HTTPS, Google session cookie
                ▼
┌──────────────────────────── BACKEND (ephemeral compute) ──────────────────────┐
│  FastAPI                                                                       │
│   /api/auth/google      OAuth code flow → session cookie                       │
│   /api/debates          POST submit (payload: idea + context + key-ref)        │
│   /api/debates/{id}/events   SSE (per-user, per-run)                           │
│   /api/debates/{id}/ack client confirms results received → BE wipes           │
│   /api/debates/{id}     GET results (for crash recovery until ACK/TTL)         │
│                                                                                │
│  Debate store (SQLite, encrypted rows): ONLY running/waiting debates           │
│    {run_id, user_id, nonce, ciphertext(payload), created_at, ttl}              │
│    payload = idea + prior context the client pushed + live results so far      │
│  Queue + worker pool (max N concurrent debates)                                │
│  Per-run RunContext: cancel flag, inbox, budget counter, BYOK key (memory only)│
└────────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 The debate lifecycle (the core contract)

```
CLIENT                                    BACKEND
──────                                    ──────
1. login (Google OAuth)
2. POST /api/debates
   {idea, prior_context?, byok?}
   ───────────────────────────────────►   validate, rate-check, budget-check
                                          encrypt payload (BE key) → store row
                                          enqueue → 202 {run_id}
3. SSE /api/debates/{run_id}/events  ◄──  stream phase/tool/result events
4. on run_finished: GET results
   write results → IndexedDB
   trigger Drive backup (encrypted)
5. POST /api/debates/{run_id}/ack
   (signed with session; includes
    results_hash the client stored)
   ───────────────────────────────────►   verify hash matches stored results
                                          DELETE debate row (payload gone)
6. crash before step 5?                   row survives; client re-pulls on next
                                          login (GET results) → ack → wiped
7. never returns / TTL expiry (7 days)?   row auto-purged; user keeps local copy
                                          if it ever got one; debate marked
                                          "expired" client-side
```

Key properties:
- **BE long-term storage ≈ zero.** After ack (normal path: seconds after finish), nothing of the user remains except an anonymized cost/usage ledger row (see §6).
- **Client is authoritative.** If the client says "I have it" (ack with hash), BE may wipe. If BE disagrees on hash, it keeps the row and returns 409 — client retries.
- **Recovery both ways:** new browser → pull backup from Drive. BE restarted mid-debate → debate state is in the encrypted row, resume like today's durable clarify pause (§4.3).

---

## 3. Identity & access

### 3.1 Google OAuth (authorization code flow, not implicit)

- **Register = first login.** No separate signup. `sub` claim (stable Google user id) is the primary key; email stored for display only.
- Backend: `GET /api/auth/google/login` → Google consent (scopes: `openid email profile` + `drive.appdata` for backup) → `GET /api/auth/google/callback` → verify code server-side → set session cookie.
- **Session cookie:** `HttpOnly; Secure; SameSite=Lax; __Host-` prefix; server-side session table (random 256-bit token hashed at rest) with 30-day sliding expiry + logout revocation. (Stateless JWT rejected: no revocation.)
- **CSRF:** `SameSite=Lax` + custom header check (`X-Requested-With`) on mutating routes; OAuth `state` parameter mandatory.
- Remove `ALLOWED_EMAILS` allowlist; replace with optional `SIGNUP_CLOSED=true` kill-switch (operator can freeze new registrations while keeping existing users).
- **Drive scope is optional at login** — requested via incremental consent only when the user enables cloud backup. Users can run fully local (no Drive) — matches principle 1.

### 3.2 Authorization rules

- Every debate route checks `row.user_id == session.user_id`. SSE stream: same check at connect time; stream dies if the run's owner changes (it can't) or session is revoked.
- No cross-user reads, ever — not even aggregates that could leak (see §7 threat model).

---

## 4. Backend redesign — the ephemeral debate engine

### 4.1 What the BE stores (and nothing more)

```
users            (user_id PK = google sub, email, created_at, flags)
sessions         (token_hash PK, user_id, expires_at)
debates          (run_id PK, user_id, state, nonce, ciphertext, created_at,
                  ttl_at, results_hash, queue_position)
usage_ledger     (user_id, day, model, calls, tokens, cost)   ← aggregated, no content
rate_limits      (in-memory buckets; optional persist)
```

**Not stored anymore:** idea texts of finished debates, transcripts, PRDs, verdicts, scores, idea trees, checkpoints, archives. The existing `data/` artifacts (archives, checkpoints, paused_runs) become per-run ephemeral and are deleted at ack. The self-improvement loop (lessons/memories) needs a decision — see Open Questions §9.

### 4.2 Encryption at rest (honest threat model)

- Each debate row: `ciphertext = AES-256-GCM(K_be, payload, nonce)`; `K_be` from env (`VENTUREBOT_DEBATE_KEY`, 32 bytes, documented rotation procedure).
- **This is NOT end-to-end encryption.** The BE holds `K_be` and decrypts in memory to run the debate. The goal is narrower and honest: *a database/file-level breach (SQL injection dump, stolen disk, leaked backup) yields ciphertext only*. An attacker who fully compromises the running process gets in-flight debates — unavoidable, since the BE must read the idea to debate it. Document this in the privacy policy.
- Payload includes a schema version + the client's pushed context so resume works after restart.

### 4.3 Resume & HITL gates (reuses what we just built)

- The durable clarify pause (`data/paused_runs/`) becomes a debate row state `WAITING_USER` — same encrypted-row mechanism, no separate file. TTL for waiting debates: 14 days, then auto-park with a "debate expired" tombstone the client understands.
- Verdict gate / PRD approval: same — worker frees its slot, row persists encrypted, user resumes from any device (their context is client-side; BE only needs the running state).

### 4.4 Queue & workers (per PUBLIC_DEPLOYMENT_DESIGN §1 — adopted unchanged)

- Worker pool N=1–2; `QUEUED → RUNNING → (WAITING_*) → DONE`; HITL waits do **not** hold worker slots; global queue cap (5) + per-user caps (2) with visible "Queue: 3/5"; per-task inboxes; per-run `RunContext` (cancel flag, state, budget) replacing the global singletons listed in PUBLIC_DEPLOYMENT_DESIGN §0.

### 4.5 Rate limiting & budget (per PUBLIC_DEPLOYMENT_DESIGN §2 — adopted, plus)

- Per-user **and** per-IP token buckets on submit/steer/auth/SSE.
- Budget: global daily cap (503 "service at capacity"), per-user daily cap (429), per-run cap (kill switch mid-run). BYOK runs bypass per-user spend caps (user pays Google directly) but still count against *rate* limits and the global concurrency ceiling.
- reCAPTCHA v3 on submit if abuse observed (defer until needed).

### 4.6 BYOK (per PUBLIC_DEPLOYMENT_DESIGN §3 — adopted unchanged)

- Key in IndexedDB, sent per-request header `X-VB-Api-Key`, memory-scoped to the run, never logged/persisted, uvicorn access-log header redaction, `_redact()` on logs + a test asserting the key never lands in any store.

---

## 5. Client design

### 5.1 IndexedDB schema (v1)

```
db: venturebot (version 1)
  ideas:        {id, title, pitch, created_at, updated_at, status}         (key: id)
  idea_runs:    {id, idea_id, run_number, started_at, finished_at, status,
                 comment, events[], research_brief, prd_text, verdict, scores}
  settings:     {byok_key?, backup_enabled, backup_updated_at, k_dev}
  sync_state:   {last_push_at, last_pull_at, pending_ack: {run_id, results_hash}}
```

- Existing JSON export/import stays as the manual escape hatch (already built).
- All debate history lives here. The UI reads **only** from IndexedDB; server calls are limited to auth + debate lifecycle.

### 5.2 Cloud backup (Drive appDataFolder)

- On enable: incremental OAuth consent for `drive.appdata`; upload single file `venturebot-backup.json` (multipart update, not create-per-sync).
- **Encryption before upload:** `K_bk` = random 256-bit key, generated on this device, stored in IndexedDB `settings.k_dev`… **key recovery problem:** if the browser is wiped, IndexedDB is gone *including K_bk*. Options:
  - **(a) v1 pragmatic:** BE escrows `K_bk` per user (stored encrypted with `K_be`). Backup is then "encrypted against DB-dump attacks" but BE can technically decrypt. Honest, zero-friction recovery.
  - **(b) v2 E2EE:** passphrase-derived key (Argon2id/PBKDF2) the user must remember; recovery = passphrase. Weaker UX, strongest privacy. Offer as opt-in "zero-knowledge backup".
  - Ship (a), design the envelope so (b) is a flag change (same blob format, different key source). Document both in privacy policy.
- Sync triggers: after each finished debate, after idea edit/delete, on tab close (best-effort), manual "Back up now" button. Debounced (≥30s between pushes).
- Restore path: new device → login → "Restore from backup" → pull, decrypt, merge into IndexedDB (last-write-wins per record by `updated_at`; conflicts practically impossible — one user, one active device for writes).

### 5.3 UI surface changes

- Login screen (Google button) → workspace loads from IndexedDB (instant) → background restore if local is empty and backup enabled.
- Settings: backup on/off + "last backed up", BYOK field, "delete all my data" (local wipe + Drive file delete + server tombstone).
- Debate submit sends `prior_context` (previous research/verdict when resuming an idea) — the BE needs it precisely because it doesn't remember.

---

## 6. Usage ledger & cost control without content

The BE keeps **only** `(user_id, day, model, calls, tokens, cost)` — enough for budget enforcement, "your usage" page, and abuse forensics; contains zero idea content. This is also what makes the free tier auditable.

---

## 7. Security review (threat model)

| # | Threat | Vector | Mitigation |
|---|--------|--------|-----------|
| 1 | BE fully compromised (RCE) | VPS breach | In-flight debates readable (accepted, documented). At rest: ciphertext only. `K_be`/escrow keys in env — rotate on incident. Blast radius = currently running debates (≤ N), not user history |
| 2 | DB dump / stolen disk / leaked backup | SQLi, disk theft, misconfigured backup | AES-GCM encrypted rows; no plaintext idea data at rest anywhere; no long-lived data to steal |
| 3 | Session theft | XSS, network | HttpOnly+Secure+SameSite cookies, no tokens in JS, TLS only, HSTS; CSP header; DOMPurify already used for all rendered agent output (kept) |
| 4 | CSRF | Cross-site POST | SameSite=Lax + custom-header check + OAuth state |
| 5 | Cross-user data leak | IDOR on debate routes | Every route: `user_id` ownership check; integration test with two sessions asserting 404/403 |
| 6 | SSE eavesdrop between users | Per-user streams | SSE endpoint authorizes run ownership at connect; events carry run_id; fan-out keyed by (user, run) |
| 7 | Key exfiltration via logs | BYOK in traces | Header redaction in uvicorn access log; `_redact()` in store.log/SSE payloads; automated test: submit with canary key, assert canary ∉ state.json, logs, archives |
| 8 | Cost abuse (free tier) | Scripted submissions | Rate limits (per-user+IP), queue caps, per-user/per-run/global budget caps, optional reCAPTCHA v3, BYOK bypass for heavy users |
| 9 | Prompt injection via idea text | Malicious idea tries to hijack agents | Existing input guard stays; agent tool surface reviewed: no tools grant filesystem/network beyond workspace sandbox |
| 10 | Backup theft (Drive) | Attacker reads user's Drive | Backup encrypted client-side (K_bk); Drive scope limited to `drive.appdata` (cannot see other files); revocable by user anytime |
| 11 | Wipe-before-client-saved | BE bug deletes results early | Wipe only on ack with matching `results_hash`; TTL fallback ≥7d; client keeps local copy regardless |
| 12 | Account takeover via Google | — | Out of our hands by design; session revocation on logout; no password reset surface exists |
| 13 | GDPR | PII on BE | Data minimization by architecture: BE holds email + usage aggregates only. Export/delete = client-side (user owns data) + BE tombstone. Privacy policy + consent at first login still required |

**Explicitly accepted risks (documented, not solved):** BE reads idea text in memory while debating (unavoidable); BE-escrowed backup key in v1 (a) until E2EE passphrase mode (b); IndexedDB wipe loses `K_bk` in mode (b) without passphrase.

---

## 8. Implementation plan (suggested order)

1. **Phase A — identity:** OAuth code flow, sessions, remove allowlist, per-user scoping of existing routes behind a feature flag. (Tests: two-user isolation.)
2. **Phase B — ephemeral BE:** encrypted debate rows, ack/wipe protocol, TTL sweeper, delete legacy stores (idea_tree/archives/checkpoints) behind the flag.
3. **Phase C — queue/workers/limits/budget/BYOK:** lift the global singletons (RunContext), adopt PUBLIC_DEPLOYMENT_DESIGN §1–3.
4. **Phase D — client:** IndexedDB migration of current server data, sync worker, Drive backup/restore, settings UI.
5. **Phase E — hardening:** security tests (canary-key leak test, two-user IDOR test, CSRF test), privacy policy + consent, load test with N=2 workers.

Each phase keeps the single-user demo working (feature flag), so the hackathon asset is never broken.

---

## 9. Open questions (decide before Phase B)

1. **Self-improvement loop vs amnesia:** lessons/dream-review currently learn from stored transcripts. Options: (a) lessons become client-side (each user's own lessons only — weaker), (b) BE distills lessons at debate end *before* wipe, keeping only anonymized lesson text (no ideas), (c) opt-in "share anonymized lessons" community pool. Lean: (b) + (c) opt-in.
2. **Backup key escrow (a) vs passphrase E2EE (b)** — ship (a) with (b) as opt-in at v2?
3. **TTL numbers:** ack-wait 7d, waiting-user 14d — reasonable?
4. Do we keep the current single-user mode as a "local-only, no login" fallback (Excalidraw-style) or force login for everyone?
5. VPS capacity: N workers × concurrent Gemini rate limits — do we need per-user model quota discovery (429 backpressure) in v1?
