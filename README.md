# VentureBot

**A self-improving, multi-agent research & development system** built on [Google ADK](https://github.com/google/adk-python).
Takes a vague idea -> researches it -> debates it (Advocate vs Critic vs Judge) -> produces a scored PRD - with a human in the loop at every consequential gate.

```
Vague Idea
  |
Orchestrator (autonomous ADK agent loop)
  +- Research Agent (google_search) -> Research Brief
  +- Creative Ideator (divergent angles, temperature 1.0)
  +- Advocate -> Critic -> Judge (asymmetric info access)
  `- [Human clarification if the idea is ambiguous - durable, survives restarts]
  |
[Human: Proceed / Rebut / Abort]
  |
PRD Writer -> Structured PRD
  |
Security Auditor (proof-read gate: hallucinations, missing NFRs, injected secrets)
  |
[Human: Approve / Changes / Reject]
  |
Scored PRD + full debate transcript
```

---

## What it is

VentureBot is a **debate-room for startup ideas**. Instead of a single LLM rubber-stamping your idea, a panel of agents with *deliberately asymmetric information access* argues it:

- the **Advocate** sees only evidence that supports the idea,
- the **Critique** sees only counter-evidence (via live `google_search`),
- the **Judge** sees both and must reach a scored verdict (PROCEED / PIVOT / PARK),
- a **Creative Ideator** runs hot to propose angles nobody asked for - and gets re-checked by the evidence-bound Critic before it can influence anything.

An autonomous **orchestrator** (ADK `LlmAgent` with function tools) drives the whole loop, with a quality gate that stops it when the PRD + verdict are stable. Every expensive or irreversible step has a **human gate**. A **self-improvement layer** (SQLite memory: session facts, lessons, techniques, idea tree) makes later debates smarter than earlier ones - without ever re-running an old debate.

## Feature highlights

- **Multi-agent debate** on Google ADK + Gemini, with structured Pydantic outputs
- **Autonomous orchestrator** with turn/tool budgets, stall detection and quality gates
- **Human-in-the-loop gates**: clarification (durable across server restarts), verdict rebuttal, PRD approval
- **Steering**: inject guidance mid-run ("focus on the EU market; ignore competitor X")
- **Idea history**: every debate is an immutable run under its idea; browse, resume, export (JSON/CSV/MD/PDF)
- **Self-improvement**: auto-captured facts, LLM-analyzed turn reviews, nightly "dream review" consolidation, idea-tree pruning
- **Safety stack**: input-injection guard, output guard (AST + secret scan), artifact scanner, sandboxed pytest (unprivileged UID + network isolation), hard daily budget, kill switch
- **Hardened dashboard**: strict CSP, server-side sessions, Google login, SSE live feed
- **Multi-user ready**: per-run workspace isolation, per-route ownership design, backend-amnesia architecture (see `notes/MULTI_USER_DESIGN.md`, `notes/MULTIUSER_SECURITY_REVIEW.md`)

---

## Architecture

### Repository layout

```
/root/venturebot/
+-- PRD.md                          <- product requirements (VB-PRD-2026-08-18)
+-- IMPLEMENTATION_PLAN.md          <- detailed build plan + task breakdown
+-- SAFETY_REVIEW.md                <- safety audit that gated all work
+-- notes/                          <- design + review docs (see below)
+-- .env                            <- live secrets (NEVER committed)
+-- .env.example                    <- env var template
|
+-- src/
|   +-- config.py                   <- env-driven config (models, budgets, paths, auth)
|   +-- dashboard.py                <- FastAPI app: SSE, auth routes, HITL, all /api/* endpoints,
|   |                                 security-headers/CSRF middleware, /static mount
|   +-- store.py                    <- JSON file-backed state (single source of truth)
|   +-- auth.py                     <- session validation + Google identity helpers
|   +-- oauth.py                    <- Google OAuth code flow: PKCE + state + nonce, BE-side exchange
|   +-- sessions.py                 <- server-side session store (sha256-hashed tokens,
|   |                                 rotation on login, logout revocation) + users table
|   +-- budget.py                   <- cumulative LLM spend enforcement
|   +-- run_manager.py              <- kill switch (StopEvent + process group kill)
|   +-- guard.py                    <- post-LLM output guard (AST check + secret scan)
|   +-- input_guard.py              <- pre-LLM injection guard + quarantine convention
|   +-- sandbox.py                  <- pytest isolation (unshare, setuid, rlimits)
|   +-- steering.py                 <- user guidance inbox (drained at checkpoints)
|   +-- url_fetch.py                <- fetches user-provided URLs for research material
|   +-- gemini_usage.py             <- Gemini token/cost tracker
|   +-- artifact_scanner.py         <- deterministic scanner + proof-read gate
|   +-- scheduler.py                <- nightly dream-review cron (APScheduler)
|   |
|   `-- agents/
|       +-- agents.py               <- LlmAgent definitions (Researcher->...->PRD Writer->Auditor)
|       +-- orchestrator.py         <- autonomous orchestrator loop + tools
|       |                             (per-run workspace: workspace/runs/{run_id}/, traversal-guarded)
|       +-- pipeline.py             <- resumable, kill-switch-aware wiring
|       +-- prompts.py              <- system prompts for all agents
|       +-- schemas.py              <- Pydantic output schemas (ResearchBrief, JudgeVerdict)
|       `-- clarify.py              <- HITL clarification tool (LongRunningFunctionTool)
|
|   `-- memory/                     <- self-improvement layer
|       +-- sqlite_store.py         <- SQLite store (facts, lessons, techniques, profile, idea tree)
|       +-- idea_tree.py            <- deterministic pruning rules (PRD section 5.5)
|       +-- auto_capture.py         <- Fork 1: persist session facts (throttled)
|       +-- review_fork.py          <- Fork 2: fire-and-forget LLM turn analysis
|       +-- dream_review.py         <- Fork 3: nightly consolidation + pruning
|       `-- _throttle.py            <- 120s per-session fork cooldown
|
+-- templates/index.html            <- dashboard SPA shell (login gate, HITL buttons, XSS-safe rendering)
+-- static/app.js                   <- dashboard application logic (externalized for strict CSP)
+-- static/vendor/                  <- pinned, self-hosted JS libs (tailwind, marked, dompurify)
+-- tests/                          <- pytest suite (149 tests)
`-- data/, state.json               <- runtime state (ideas DB, archives, checkpoints)
```

### Runtime architecture

```
Browser (SPA) ---- HTTPS ---- nginx ---- uvicorn (FastAPI, 127.0.0.1:8090)
                                |
                    +-----------+------------+
                    |  security middleware   |  CSP script-src 'self', XCTO,
                    |  (headers + CSRF)      |  Referrer-Policy, frame-ancestors none,
                    |                        |  HSTS; Sec-Fetch-Site check on mutations
                    `-----------+------------+
                                |
        +-----------------------+---------------------------+
        |                       |                           |
  Google OAuth             session store                debate engine
  (code flow, PKCE,        (SQLite: sha256 token        (ADK orchestrator + agents,
   state+nonce; secret     hashes, rotation,             per-run workspaces, budget,
   stays server-side)      revocation, users table)      kill switch, HITL gates)
                                |                           |
                          state.json /                Gemini API (google_search
                          data/venturebot.db          grounded research), memory
                          (ideas, runs, archives)     store, sandboxed tools
```

---

## Key design decisions

### 1. Custom orchestrator over SequentialAgent
The debate is not a fixed pipeline: the orchestrator decides per turn which sub-agent to call (research again? creative angle? straight to PRD?), with budgets (`ORCHESTRATOR_MAX_TURNS`, `MAX_TOOL_CALLS`) and a quality gate that stops on PRD + verdict + stall detection. Patterned after the ADK samples' `AgentTool` delegation (see Credits).

### 2. Asymmetric information access (blind debate)
Advocate and Critic see disjoint slices of evidence. This is the core product idea - it produces real argument instead of agreeable summarization, and the Judge's verdict cites both sides.

### 3. Fail loud, fail honest
Verdict parsing raises instead of silently PARKing; the artifact scanner never auto-passes; the budget enforcer blocks pre-call. A debate that can't be completed says so.

### 4. Security as architecture, not afterthought
Driven by an adversarial security review (`notes/MULTIUSER_SECURITY_REVIEW.md`, 6 design + 7 implementation findings, all P0s fixed):

- **Per-run workspace isolation** - orchestrator file tools resolve strictly inside `workspace/runs/{run_id}/`; path traversal and cross-run access are blocked (a malicious prompt can't read another debate's files).
- **Strict CSP + vendored JS** - `script-src 'self'`; marked/DOMPurify/Tailwind are pinned copies served from `/static/vendor/`, so a CDN compromise can't inject scripts. All inline event handlers removed.
- **Log redaction at source** - the process log (journald) gets metadata only; debate content never leaves state.json.
- **Server-side sessions** - cookies carry opaque random tokens; only sha256 hashes are stored; every login rotates; logout revokes. Session fixation is structurally impossible.
- **OAuth done per OAuth 2.1** - authorization-code flow with PKCE, single-use state + nonce, secret server-side, no refresh tokens stored.
- **CSRF beyond SameSite** - cross-site mutating requests are rejected via Sec-Fetch-Site.
- **Planned (multi-user phase B)**: backend amnesia (ephemeral encrypted debate rows, ACK-before-wipe), per-route ownership with 404-not-403, BYOK keys in memory only, lesson-poisoning moderation. See `notes/MULTIUSER_TASKS.md` for the live task list.

### 5. Local-first data ownership (target architecture)
The design end-state keeps user data in the user's browser (IndexedDB + encrypted Google Drive `appDataFolder` backup); the backend stays an ephemeral compute node. The backend-amnesia trade-offs (including the v1 backup-key escrow caveat) are documented honestly in `notes/MULTI_USER_DESIGN.md`.

---

## Install & Run

### Prerequisites

- Linux (systemd + nginx assumed for deployment), Python 3.12
- A Google AI Studio API key (`GOOGLE_API_KEY`) for Gemini
- (optional, for login) A Google Cloud OAuth client - see next section

### Local setup

```bash
git clone <repo> venturebot && cd venturebot
python3 -m venv venv
./venv/bin/pip install -r requirements.txt   # google-adk, fastapi, uvicorn, apscheduler, google-auth, dotenv

cp .env.example .env
# fill in GOOGLE_API_KEY (required); auth vars optional for local dev
```

```bash
# development server (no login required by default: VENTUREBOT_NO_AUTH=1)
./venv/bin/uvicorn src.dashboard:app --host 127.0.0.1 --port 8090
# open http://127.0.0.1:8090
```

### Google OAuth setup (client secret)

To enable real login you need a Google Cloud OAuth client (web). If you already have a client ID, you only need its secret + redirect URI:

1. Open [console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials) and pick the project that contains your client ID.
2. Click your OAuth 2.0 Client ID -> **Authorized redirect URIs -> ADD URI**:
   ```
   https://venturebot.taskmind-ai.com/api/auth/callback   # or your own domain
   ```
   Save.
3. Copy the **Client secret** from the same page (or create one if the client has none: *Credentials -> + CREATE CREDENTIALS -> OAuth client ID -> Web application*, then use that client's ID + secret pair).
4. In `.env`:
   ```
   GOOGLE_CLIENT_ID=...apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=GOCSPX-...
   VENTUREBOT_PUBLIC_BASE_URL=https://venturebot.taskmind-ai.com   # builds the redirect URI
   VENTUREBOT_COOKIE_SECURE=true                                   # you're behind HTTPS
   VENTUREBOT_ALLOWED_EMAILS=you@gmail.com                         # optional allowlist
   ```
5. Check **APIs & Services -> OAuth consent screen**: publish the app or add your email as a test user (else Google shows an "unverified app" warning).
6. Enable login:
   ```bash
   # in .env: VENTUREBOT_NO_AUTH=0
   sudo systemctl restart venturebot
   ```
   The app now shows **Sign in with Google**; after consent you land back on the dashboard signed in. **Sign out** revokes the server-side session.

Notes: the secret never leaves the backend (the code exchange happens server-side). To rotate it, reset it on the same credentials page and update `.env`. To freeze new registrations without locking out existing users, set `VENTUREBOT_SIGNUP_CLOSED=true`.

### Production deployment (current: VPS + systemd + nginx)

The app runs as a systemd service behind nginx with TLS (Let's Encrypt). SSE requires `proxy_buffering off` and a long read timeout.

```ini
# /etc/systemd/system/venturebot.service
[Unit]
Description=VentureBot Dashboard
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/venturebot
ExecStart=/root/venturebot/venv/bin/uvicorn src.dashboard:app --host 127.0.0.1 --port 8090
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```nginx
# /etc/nginx/sites-enabled/venturebot.example.com
server {
    server_name venturebot.example.com;
    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;   # used for OAuth redirect URI
        proxy_buffering off;                          # required for SSE
        proxy_read_timeout 3600s;
    }
    listen 443 ssl;   # + certificates (certbot)
}
```

```bash
sudo systemctl enable --now venturebot
```

### GCP deployment (Cloud Run + CI/CD) — pipeline ready, cloud setup pending

The repo ships a production container (`Containerfile`) and GitHub Actions:

- **CI** (`.github/workflows/ci.yml`): runs the full pytest suite on every push/PR — active now.
- **CD** (`.github/workflows/deploy.yml`): on push to `main`, builds the image,
  pushes to Artifact Registry and deploys Cloud Run via Workload Identity
  Federation (keyless). It stays dormant (with a notice) until the one-time
  GCP setup from **`notes/GCP_DEPLOYMENT.md`** is done.
- **State persistence**: `scripts/data_snapshot.py` restores/pushes a snapshot
  of `data/` to GCS around deploys (`max-instances=1` until Phase B removes
  persistent state entirely).

Follow `notes/GCP_DEPLOYMENT.md` sections 1-5 (~15 minutes of one-time setup)
then every merge to `main` ships automatically.

### Stub server (UI tuning, zero LLM cost)

```bash
./venv/bin/uvicorn src.stub_server:app --host 127.0.0.1 --port 8091
```

Same API surface as the real dashboard, with a canned debate - useful for UI work without burning tokens.

### Run tests

```bash
./venv/bin/python -m pytest tests -q     # 149 tests
```

---

## Using VentureBot

1. **Submit an idea** - type it into the debate box (optionally add URLs for research material) and hit **▶ Debate**.
2. **Watch the debate live** - the feed streams every agent turn over SSE.
3. **Answer clarifications** - if the idea is ambiguous, the orchestrator pauses with a question; your answer persists across restarts (durable pause).
4. **Steer mid-run** - use the steering box ("focus on B2B; ignore enterprise"); it's injected at the next checkpoint.
5. **React to the verdict** - PROCEED ANYWAY / REBUT (with your counter-arguments) / ABORT.
6. **Approve the PRD** - the Security Auditor proof-reads it first; approve, request changes, or reject.
7. **Browse idea history** - every run is archived under its idea: transcript, PRD, scores. Export as JSON/CSV/Markdown/PDF; re-run or resume any idea.
8. **Self-improvement console** - inspect captured lessons/techniques; run the nightly **Dream Review** manually anytime.
9. **Budget** - the daily spend cap shows in the header; raise it in-session if needed. **⏹ Stop** is the kill switch.

---

## API endpoints (selection)

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/auth/login` | GET | Start Google OAuth (302 to Google; PKCE) |
| `/api/auth/callback` | GET | OAuth callback -> session cookie |
| `/api/auth/me` | GET | Current user / auth status |
| `/api/auth/logout` | POST | Revoke server-side session |
| `/api/run-phase1` | POST | Start a debate for an idea |
| `/api/state` | GET | Live pipeline state (also via SSE `/api/events`) |
| `/api/steering` | POST | Inject mid-run guidance |
| `/api/clarify/answer` | POST | Answer a durable clarification pause |
| `/api/resume` | POST | Resume verdict/PRD gate |
| `/api/ideas` | GET | Idea history (facets, pagination) |
| `/api/ideas/export` | GET | Full JSON backup (all ideas + runs) |
| `/api/ideas/import` | POST | Restore from JSON export |
| `/api/usage` | GET | Token/cost usage |
| `/api/budget/raise` | POST | Raise daily budget cap |
| `/api/stop` | POST | Kill switch |
| `/scheduler/dream-review` | POST | Run memory consolidation now |

Mutating endpoints reject cross-site browser requests (CSRF); non-browser clients (curl/scripts) work unchanged.

---

## Environment variables

See `.env.example` for the full annotated template. The essentials:

```bash
# Required
GOOGLE_API_KEY=            # Gemini (AI Studio)

# Auth (optional locally; required for login)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
VENTUREBOT_PUBLIC_BASE_URL=
VENTUREBOT_COOKIE_SECURE=false
VENTUREBOT_ALLOWED_EMAILS= # optional allowlist
VENTUREBOT_SIGNUP_CLOSED=false
VENTUREBOT_NO_AUTH=1       # 1 = no-login prototype mode; 0 = Google login enforced

# Budget
VENTUREBOT_DAILY_BUDGET_LIMIT=20.00

# Models (all configurable; defaults are Gemini)
VENTUREBOT_MODEL_RESEARCHER / _ADVOCATE / _CRITIC / _JUDGE / _PRD_WRITER / _AUDITOR / _CREATIVE / _ORCHESTRATOR

# Paths (all optional)
VENTUREBOT_WORKSPACE / _STATE / _DATA / _DB / _CHECKPOINT_DIR / _ARCHIVE_DIR / _SANDBOX
```

---

## Status & roadmap

**Phase 1 (debate -> PRD) is complete and deployed.** Multi-user hardening P0s are done (see `notes/MULTIUSER_TASKS.md`).

| Milestone | Status |
|-----------|--------|
| M0.5 Safety baseline | ✅ kill switch, sandbox, budget, guards, auditor |
| M1 Phase 1 core debate | ✅ ADK agents + autonomous orchestrator + HITL |
| M2 Observable UI | ✅ SSE, HITL buttons, idea history, exports |
| M3 Self-improvement | ✅ memory store, capture, review forks, dream review |
| Security hardening (multi-user P0) | ✅ workspace isolation, CSP, log redaction, OAuth+PKCE, server-side sessions, CSRF |
| Multi-user data plane (Phase B) | 🚧 tenancy keys in place; ownership checks, ephemeral debate engine, BYOK pending |
| GCP deployment (Cloud Run + CI/CD) | 🚧 pipeline built + container tested; cloud one-time setup pending (`notes/GCP_DEPLOYMENT.md`) |

---

## Documentation

- `PRD.md` - full product requirements
- `IMPLEMENTATION_PLAN.md` - build plan incl. which ADK samples each part is patterned on
- `SAFETY_REVIEW.md` - safety audit that gated all work
- `notes/MULTI_USER_DESIGN.md` - multi-user end-to-end design (local-first hybrid)
- `notes/MULTIUSER_SECURITY_REVIEW.md` - adversarial security review (findings + fix list)
- `notes/MULTIUSER_TASKS.md` - live multi-user task list + implementation log
- `notes/PUBLIC_DEPLOYMENT_DESIGN.md` - queue/rate-limit/BYOK mechanics
- `notes/LOOP_ARCHITECTURE_V2.md` - orchestrator loop design (self-improvement design is covered in `PRD.md` section 5 and `notes/IMPLEMENTATION_PLAN.md`)
- `CODE_REVIEW_FINAL.md`, `PLAN_REVIEW.md` - internal reviews

---

## Credits & acknowledgments

VentureBot stands on the shoulders of:

- **[Google ADK](https://github.com/google/adk-python)** (Agent Development Kit) - the agent runtime, `LlmAgent`, `AgentTool` delegation, `LongRunningFunctionTool`, session services, and Gemini integration.
- **[google/adk-samples](https://github.com/google/adk-samples)** - several agents here are patterned directly on samples from this repo (per `notes/IMPLEMENTATION_PLAN.md` section 1.4):
  - `llm-auditor/` - sequential critic chain with grounding (`google_search`) and callbacks -> our Critic + Security Auditor pattern
  - `academic-research/` - `AgentTool` delegation + google_search integration -> our orchestrator's sub-agent tools
  - `financial-advisor/` - `response_schema` enforcement -> our Pydantic verdict/brief schemas
  - `sdlc-task-planner/` + `sdlc-technical-designer/` - structured-output prompt style -> our PRD writer prompts
  - `customer-service/` - human-in-the-loop handoff patterns -> our clarify/verdict/PRD gates
  - `travel-concierge/eval/` - eval infrastructure pattern -> our test approach for agent flows
  - `cross-session-memory/` (core samples) - `PreloadMemoryTool` + after-agent-callback wiring and Memory Bank config -> our memory preload + self-improvement forks
- **[FastAPI](https://fastapi.tiangolo.com)** / **[Starlette](https://www.starlette.io)** - the dashboard backend and SSE.
- **[Tailwind CSS](https://tailwindcss.com)** (Play runtime, pinned 3.4.16), **[marked](https://github.com/markedjs/marked)** (12.0.2) and **[DOMPurify](https://github.com/cure53/DOMPurify)** (3.2.4) - self-hosted under `static/vendor/` so the strict CSP holds without third-party requests at runtime.
- **[APScheduler](https://github.com/agronholm/apscheduler)** - nightly dream-review scheduling.
- **[google-auth](https://github.com/googleapis/google-auth-library-python)** - id_token verification in the OAuth flow.

---

## License

Private - hackathon submission.

## Contact

Built for the Google ADK Hackathon 2026. Track 2: The Collaborative Partner.
