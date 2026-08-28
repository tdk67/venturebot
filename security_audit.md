# Idea Lint — Security & Compliance Audit Report
**Date:** 2026-08-28 | **Scope:** Full codebase + legal compliance (GDPR/DSGVO, EU AI Act, German TMG/Impressumspflicht)
**Standard:** OWASP Top 10, BSI IT-Grundschutz, EU AI Act (Regulation EU 2024/1689), GDPR Art. 5/6/13/25/35

---

## Executive Summary

| Layer | Overall Risk |
|---|---|
| API / Network Security | **Medium** — strong foundation; 2 gaps |
| Secret / Key Handling | **Low** — well-designed BYOK model |
| Output & Input Sanitization | **Low** — multiple defense layers present |
| Data Persistence & Privacy | **Low-Medium** — good ephemeral design; 2 gaps |
| GDPR / DSGVO Compliance | **High Risk** — missing legal documents and consent flow |
| EU AI Act (2024/1689) | **Medium** — transparency and human oversight gaps |
| German Law (TMG / Impressumspflicht) | **Critical** — Impressum link exists but no page served |

---

## Part 1 — Technical Security Findings

### What is correctly implemented

| Item | Assessment |
|---|---|
| BYOK — no server-side key storage | Confirmed: keys are in-memory per-run only |
| Key redaction in errors and events | `_redact()` applied to all error strings and event payloads |
| Key scrubbed in `finally` block | `rec.api_key = ""` guaranteed on run end/fail |
| Content-Security-Policy | Strict same-origin; no `unsafe-eval`; no CDN scripts |
| X-Content-Type-Options: nosniff | Present |
| X-Frame-Options: DENY | Present |
| Referrer-Policy | strict-origin-when-cross-origin |
| Rate limiting (per-IP, hourly) | 20 runs/hour, 1 concurrent, 3 SSE/IP |
| Request body size cap | 32 KiB |
| UUIDv4 run IDs (unguessable) | 122-bit random space |
| No run enumeration | No listing API; only lookup-by-ID |
| Input prompt injection guard | `input_guard.py` + quarantine wrapper |
| Output code scanner | AST-level import and call blocklist |
| Ephemeral TTL store | 24h TTL, client ACK to 410, then wipe |

---

### SEV-1 Critical

#### C1 — Impressum route `/impressum` returns 404
**File:** [`landing.html:262`](file:///c:/Data/work/genAI/venturebot/templates/landing.html#L262), [`dashboard.py`](file:///c:/Data/work/genAI/venturebot/src/dashboard.py) (route missing)
**Risk:** §5 TMG (Telemediengesetz) requires a reachable Impressum. Publishing a link to a 404 is a legal violation in Germany. Competitors and Abmahnanwalte actively scan for this. Fines up to €50,000.
**Fix:** Create a `/impressum` route serving a valid Impressum page with: full legal name, address, email, VAT-ID (if applicable), Handelsregisternummer (if applicable).

---

### SEV-2 High

#### H1 — Missing `Strict-Transport-Security` (HSTS) header
**File:** [`dashboard.py:122-129`](file:///c:/Data/work/genAI/venturebot/src/dashboard.py#L122-L129)
**Risk:** Without HSTS, a downgrade-to-HTTP attack can intercept the BYOK API key in transit on the first request. The API key is transmitted in a plain JSON body (`POST /api/debates`). BSI TL-03183 requires HSTS for any service handling credentials.
**Fix:** Add `response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"` to the middleware, gated on `VENTUREBOT_COOKIE_SECURE=true` (prod-only).

#### H2 — Missing `Permissions-Policy` header
**File:** [`dashboard.py:122-129`](file:///c:/Data/work/genAI/venturebot/src/dashboard.py#L122-L129)
**Risk:** Without Permissions-Policy, browser features like microphone, camera, geolocation, and USB are not explicitly disabled, leaving unnecessary attack surface.
**Fix:** Add `response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), usb=()"`.

#### H3 — Pause files contain full idea text with no expiry TTL
**File:** [`orchestrator.py:261-265`](file:///c:/Data/work/genAI/venturebot/src/agents/orchestrator.py#L261-L265) (`write_pause`)
**Risk:** Pause files written to `data/pauses/<run_id>.json` contain the full idea text, research brief, advocate/critic arguments, and the last 100 debate events. These persist indefinitely if the user never answers the clarification question. On Cloud Run (ephemeral filesystem), risk is low. On a VPS or persistent volume, this violates GDPR Art. 5(1)(e) ("storage limitation").
**Fix:** Add a pause file TTL sweeper (e.g., 7 days). Log a warning when pause files older than 24h exist. Document the data retention period.

#### H4 — `X-Forwarded-For` trusted without proxy validation for rate limiting
**File:** [`rate_limit.py:59-76`](file:///c:/Data/work/genAI/venturebot/src/rate_limit.py#L59-L76) (`client_ip`)
**Risk:** The function trusts the first hop of `X-Forwarded-For` without verifying the request comes through a trusted proxy. An attacker could forge `X-Forwarded-For: 1.2.3.4` to spoof their IP and bypass per-IP rate limits.
**Fix:** Configure Cloud Run or nginx to strip and rewrite the header. In code, only accept `X-Forwarded-For` if the direct peer is a known private IP range (10.x, 172.16-31.x, 192.168.x, ::1).

---

### SEV-3 Medium

#### M1 — No `X-Request-ID` / correlation ID in API responses
**Risk:** Without a request correlation ID it is impossible to correlate server-side errors with client-side crash reports, making incident response slow. Also needed for audit logs under GDPR Art. 5(2) (accountability).
**Fix:** Add a `X-Request-ID` middleware that generates a UUID per request and adds it to the response header and structured log line.

#### M2 — API key stored in `localStorage` (not `sessionStorage`)
**File:** `frontend/src/byok.ts`
**Risk:** `localStorage` is permanent — the key persists across sessions. On shared/public computers, a forgotten key can be accessed by any JavaScript on the origin (XSS), a malicious extension, or physical access. The strict CSP significantly mitigates XSS risk but physical access remains.
**Recommendation:** Offer a "Remember Key" vs "This Session Only" toggle; default to `sessionStorage`.

#### M3 — Idea text could leak into full-body request logs
**Risk:** If full-body request logging is ever enabled (common in debugging), the `POST /api/debates` body containing the idea in plaintext would flow into GCP Cloud Logging (30-day retention) without a documented lawful basis under GDPR Art. 6.
**Fix:** Document explicitly that full-body request logging must never be enabled. Add to the operator runbook.

#### M4 — Clarification endpoint receives API key + idea answer together
**File:** `src/dashboard.py` (`/api/debates/{run_id}/clarify`)
**Risk:** If the API key leaks from the clarification request, it leaks the user's strategic startup idea alongside it — two sensitive items coupled.
**Fix:** Consider using a short-lived session token after initial key validation, separating re-authentication from the answer payload.

#### M5 — Pause directory created with default OS permissions
**File:** [`orchestrator.py:226-229`](file:///c:/Data/work/genAI/venturebot/src/agents/orchestrator.py#L226-L229) (`_pause_dir`)
**Risk:** `d.mkdir(parents=True, exist_ok=True)` uses the process umask. On a VPS with a shared root user, other processes could read the pause files containing idea text.
**Fix:** Use `d.mkdir(parents=True, exist_ok=True, mode=0o700)`.

---

### SEV-4 Informational

#### I1 — No `robots.txt`
Crawlers may index the `/app` UI. Low risk given SSE-only data, but good hygiene.

#### I2 — `~/.pi/agent/auth.json` key fallback in production
**File:** [`config.py:144-152`](file:///c:/Data/work/genAI/venturebot/src/config.py#L144-L152)
A developer-convenience path that reads an OpenRouter API key from the home directory. If this path is readable inside the container it could be a secret leak vector. Should be disabled via an env check in production.

---

## Part 2 — GDPR / DSGVO Compliance (German Market)

> **Jurisdiction:** Germany — GDPR applies as directly applicable EU law, supplemented by BDSG (Bundesdatenschutzgesetz).

### Critical Compliance Gaps

| Article | Requirement | Status |
|---|---|---|
| Art. 13 | Privacy Notice at point of collection | **Missing** — no privacy policy in app or on landing page |
| Art. 5(1)(b) | Purpose Limitation | No documented purpose statement |
| Art. 5(1)(e) | Storage Limitation | Pause files have no max TTL (see H3) |
| Art. 6 | Lawful Basis | No documented legal basis for any processing |
| Art. 25 | Privacy by Design / Default | IndexedDB local storage is good. `localStorage` API key is not (see M2) |
| Art. 35 | DPIA required if high-risk processing | AI-driven analysis of business ideas likely qualifies |
| Art. 37 | Data Protection Officer | Not required at hackathon scale |

### What Personal Data Does Idea Lint Process?

Even with a near-stateless architecture, the following **personal data** is processed (Art. 4 GDPR):

| Data | Where | Risk Level |
|---|---|---|
| Idea text (may contain founder name, address, personal plans) | Server RAM + Pause files (disk) + Google Gemini API | High |
| API Key (pseudonymous identifier) | Browser localStorage + RAM | Medium |
| IP Address | RAM (rate limiter, ephemeral) | Medium |
| Idea content sent to Google Gemini | Google's infrastructure (US-based by default) | High — needs SCCs or DPA |

### Minimum Required Actions for GDPR Compliance

1. **Privacy Policy (Datenschutzerklarung)** — add a linked, accessible page explaining:
   - What data is collected (IP, idea text, API key)
   - Who processes it (operator + Google Gemini as data processor)
   - How long it is retained (run TTL 24h, pause files up to X days)
   - Data subject rights (erasure, access, portability)
   - Legal basis: Art. 6(1)(b) — contract performance, or (f) — legitimate interest

2. **Google Data Processing Agreement** — complete a DPA with Google under Art. 28 GDPR via [cloud.google.com/terms/data-processing-addendum](https://cloud.google.com/terms/data-processing-addendum).

3. **International Transfer** — Gemini API calls pass idea content to Google's infrastructure (may process in the US). Requires either:
   - Reliance on Google's Standard Contractual Clauses (SCCs), or
   - Configuring an EU Gemini endpoint (`europe-west3`)

4. **Storage Notice** — §25 TTDSG requires disclosure of `localStorage`/`IndexedDB` use. Add a one-line notice: *"This app stores your API key locally in your browser. No tracking cookies are set."*

---

## Part 3 — EU AI Act (Regulation EU 2024/1689)

> In force August 2024. High-risk/GPAI obligations apply from August 2026.

### Risk Classification

| Criteria | Assessment |
|---|---|
| AI System Type | GPAI deployer (uses Gemini). Subject to GPAI transparency obligations. |
| High-Risk Category (Annex III)? | Not clearly high-risk. Assists with business decisions but does not replace human judgment in regulated domains. |
| Prohibited Practice (Art. 5)? | None — no social scoring, subliminal manipulation, or biometric categorization. |

### Required Actions

#### Article 50 — Transparency for AI-Interacting Systems
**Status: Partially missing**
The UI shows agent names (Researcher, Judge, etc.) which is good, but there is no explicit disclosure at the point of use.
**Fix:** Add a banner: *"Idea Lint uses multi-agent AI (Google Gemini) to analyze your idea. All outputs are AI-generated and must be independently verified before making business decisions."*

#### Article 13 — GPAI Transparency
**Status: Partial**
Deployers must pass through transparency information about the underlying GPAI to users.
**Fix:** In the Privacy Policy / Impressum, disclose which AI models are used and link to Google's EU AI Act transparency documentation.

#### Article 14 — Human Oversight
**Status: Well implemented**
The HITL clarification gates and verdict override are correctly implemented.
**Recommendation:** Surface this to users: *"The AI Judge's verdict is advisory. You can override it at any time."*

#### Article 9 — Risk Management Documentation
**Status: Missing**
A brief documented risk assessment is required for any EU AI deployment.
**Fix:** Write a short risk assessment (intended purpose, foreseeable misuse, mitigations).

---

## Part 4 — German-Specific Legal Requirements

### §5 TMG / §5 DDG — Impressumspflicht (CRITICAL)

**The Impressum link on the landing page leads to a 404.**

The Impressum must be reachable within 2 clicks from any page and must contain:
- Full legal name (Vor- und Nachname or company name)
- Physical postal address (PO box is insufficient)
- Email address with response within 24 hours
- For companies: Handelsregister, Amtsgericht, Registernummer
- If VAT registered: USt-IdNr.

**Immediate action:** Implement `/impressum` or remove the broken link.

### §25 TTDSG — Storage Consent

`localStorage` and `IndexedDB` usage requires either explicit consent or documentation that the functional necessity exception applies (§25(2) TTDSG). Document that API key storage is strictly necessary for the service to operate.

### §5 UWG — Misleading Claims

Claims like "Unlimited debates" should be factually accurate or appropriately qualified to avoid issues under German competition law.

---

## Remediation Roadmap

### P0 — Before any public launch (legal liability)

| # | Action | Effort |
|---|---|---|
| 1 | Implement `/impressum` route with valid legal information | 2h |
| 2 | Publish GDPR-compliant Privacy Policy (Datenschutzerklarung) | 4h |
| 3 | Add EU AI Act transparency disclosure in the UI | 1h |
| 4 | Add HSTS header to security middleware | 30min |

### P1 — Before handling significant user volume

| # | Action | Effort |
|---|---|---|
| 5 | Add `Permissions-Policy` header | 30min |
| 6 | Sign Google DPA (online form) | 30min |
| 7 | Add pause file TTL sweeper (7-day max) | 2h |
| 8 | Fix `_pause_dir()` to `mode=0o700` | 5min |
| 9 | Add `X-Request-ID` correlation middleware | 1h |
| 10 | Add §25 TTDSG storage disclosure | 1h |

### P2 — Hardening & Best Practice

| # | Action | Effort |
|---|---|---|
| 11 | Add `X-Forwarded-For` trusted-proxy validation | 2h |
| 12 | Offer `sessionStorage` option for API key | 2h |
| 13 | Write and publish brief AI Act risk assessment | 4h |
| 14 | Configure EU Gemini endpoint (`europe-west3`) | Config change |
| 15 | Disable `~/.pi/agent/auth.json` key fallback in production | 30min |

---

> **Disclaimer:** This is a technical security assessment, not legal advice. For binding legal opinion on GDPR, EU AI Act, and TMG/DDG obligations in the German market, consult a qualified attorney specializing in IT-Recht and Datenschutzrecht.
