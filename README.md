# Idea Lint

**A self-improving, multi-agent research & development system** built on [Google ADK](https://github.com/google/adk-python).
Takes a startup idea -> researches it (with live Google search grounding) -> debates it (Advocate vs Critic vs Judge) -> produces an actionable, scored PRD with a human in the loop at every consequential gate.

```
Startup Idea
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

Idea Lint is a **debate-room for startup ideas**. Instead of a single LLM rubber-stamping your idea, a panel of agents with *deliberately asymmetric information access* argues it:

- the **Advocate** sees only evidence that supports the idea,
- the **Critic** sees only counter-evidence (via live `google_search`),
- the **Judge** sees both and must reach a scored verdict (PROCEED / PARK / PRUNE),
- a **Creative Ideator** runs hot to propose pivots and niches - and gets re-checked by the evidence-bound Critic before it can influence anything.

An autonomous **orchestrator** (ADK `LlmAgent` with function tools) drives the whole loop, with a quality gate that stops it when the PRD + verdict are stable. Every expensive or irreversible step has a **human gate**.

### Bring Your Own Key (BYOK) & Privacy-First Architecture
Idea Lint uses a **BYOK (Bring Your Own Key)** architecture:
- Users provide their own **Google Gemini API Key** (`AIza...`) or OpenRouter key (`sk-or-v1-...`) directly in the UI Settings modal.
- The API key is stored securely in the browser's IndexedDB and LocalStorage, never persisted to a server database.
- Ideas, debate transcripts, and PRDs live exclusively in the browser's client-side store with one-click JSON and Markdown export.

---

## Quick Start (Run Locally)

### 1. Prerequisites
- **Python 3.12+**
- **Node.js 18+** (for bundling the TypeScript frontend)
- A **Google Gemini API Key** from [Google AI Studio](https://aistudio.google.com/app/apikey) (or OpenRouter key)

### 2. Install Dependencies

#### Backend
```bash
# Create and activate virtual environment
python -m venv .venv

# On Linux/macOS:
source .venv/bin/activate
pip install -r requirements.txt

# On Windows (PowerShell):
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

#### Frontend (TypeScript SPA)
```bash
cd frontend
npm install
npm run build
cd ..
```

### 3. Start the Server

```bash
# On Linux/macOS:
python -m uvicorn src.dashboard:app --host 127.0.0.1 --port 8090 --reload

# On Windows (PowerShell):
.venv\Scripts\python.exe -m uvicorn src.dashboard:app --host 127.0.0.1 --port 8090 --reload
```

### 4. Open the App
- Open your browser to: **`http://127.0.0.1:8090/`** (Landing page) or **`http://127.0.0.1:8090/app`** (Command Center)
- Click **⚙️ Settings** or the **🔑 API Key** badge in the header.
- Paste your Google Gemini API key (`AIza...`) and click **Validate & Save**.
- Enter your startup idea in the pitch box and click **🚀 Start Debate**!

---

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

### GCP deployment (Cloud Run + CI/CD)

**Live at:** `https://venturebot-442488405067.europe-west3.run.app`

The repo ships a production container (`Containerfile`) and GitHub Actions:

- **CI** (`.github/workflows/ci.yml`): runs the full pytest suite on every push/PR.
- **CD** (`.github/workflows/deploy.yml`): on push to `main`, builds the image,
  pushes to Artifact Registry and deploys to Cloud Run via Workload Identity
  Federation (keyless).
- **State persistence**: `scripts/data_snapshot.py` restores/pushes a snapshot
  of `data/` to GCS around deploys (`max-instances=1` until Phase B removes
  persistent state entirely).

**One-time setup** (2 ways):

| Method | Command | Best for |
|---|---|---|
| **Setup script** | `./scripts/setup.sh` | Quick, one project |
| **Terraform** | `cd terraform && terraform apply` | Teams, reproducibility, multiple envs |

Both are fully documented in **`notes/GCP_DEPLOYMENT.md`**. After setup, every
merge to `main` deploys automatically.

### Terraform quick-start

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your project ID, secrets, etc.

terraform init
terraform plan    # review what will be created
terraform apply   # deploy everything
```

Terraform creates: APIs, service accounts, IAM roles, Artifact Registry,
Secret Manager secrets, GCS bucket, WIF pool + provider, and the Cloud Run
service  -- all in one command. Idempotent. Run `terraform destroy` to tear down.

### Cost model

| Resource | Idle cost | Active cost | Notes |
|---|---|---|---|
| Cloud Run | $0/month | ~$0.00002/request | Scale-to-zero; 0 instances = 0 cost |
| Artifact Registry | ~$0.10/month | same | ~0.1 GB of images |
| Secret Manager | $0.06/secret/month | same | 2 secrets = $0.12/month |
| GCS bucket | ~$0.01/month | same | State snapshots, negligible |
| **Total infra** | **~$0.23/month** | **~$0.23/month** | Idle cost is near-zero |

**LLM costs** (the real cost driver):

| Model | Input / 1M tokens | Output / 1M tokens | Per debate (est.) |
|---|---|---|---|
| gemini-3.7-flash | $0.10 | $0.40 | ~$0.02 |
| gemini-3.1-pro-preview | $1.25 | $10.00 | ~$0.30 |

A typical debate uses ~4 flash calls + ~3 pro calls ≈ **$0.50–$1.00 per debate**.
The app enforces a **daily budget cap** (default $20) that blocks new LLM
calls when exceeded. Raise it in-app via the UI.

**Cloud Run scaling:**

- `max-instances=1` (for state consistency until Phase B)
- `containerConcurrency=80` (80 concurrent requests per instance)
- `min-instances=0` (scales to zero when idle  -- no cost)
- Scale-to-zero cold start: ~2–5 seconds

There is no built-in Cloud Run rate limiting. For public launch, the app-level
queue/rate-limit model is designed in `notes/PUBLIC_DEPLOYMENT_DESIGN.md`
(not yet implemented).

### Custom domain (Firebase Hosting  -> Cloud Run)

Cloud Run's built-in `domain-mappings` are **not supported in `europe-west3`**,
so we front the service with **Firebase Hosting**  -- a free, GA, Google-native
CDN that rewrites the custom domain to the Cloud Run service. Frankfurt
(`europe-west3`) is on Firebase's supported Cloud Run rewrite region list.

Example domain: `venture-bot.taskmind-ai.com`.

**1. Create the Firebase site (owner, one-time, ~2 min):**

```bash
# In the Firebase console: https://console.firebase.google.com
#   Add project  -> select "venturebot-506408"  -> skip Analytics  -> Create
# Then enable Hosting:
#   Build  -> Hosting  -> Get started  -> create a Site (ID: venturebot)
```

**2. Point DNS at Firebase (Hostinger / your provider):**

Remove the old `CNAME ghs.googlehosted.com` and add Firebase's A records:

```
Type:  A
Name:  venture-bot
Value: 199.36.158.100

Type:  A
Name:  venture-bot
Value: 199.36.158.101
```

(Use the exact records the Firebase console shows after you click
"Add custom domain"  -- the console will issue a TXT ownership record too.)

**3. Deploy the hosting rewrite:**

```bash
firebase login          # your owner Google account
cd venturebot
firebase deploy --only hosting
```

`firebase.json` rewrites `**`  -> Cloud Run `venturebot` in `europe-west3`.

**4. Update env vars & OAuth:**

```bash
gcloud run services update venturebot --region=europe-west3 \
  --update-env-vars="VENTUREBOT_PUBLIC_BASE_URL=https://venture-bot.taskmind-ai.com,GOOGLE_CLIENT_ID=<your-client-id>"
```

Add `https://venture-bot.taskmind-ai.com/api/auth/callback` to your Google
OAuth client's authorized redirect URIs.

**5. Update GitHub Actions:**

Set the `PUBLIC_BASE_URL` and `GOOGLE_CLIENT_ID` variables in the repo:
`https://github.com/tdk67/venturebot/settings/variables/actions`  -> 
`PUBLIC_BASE_URL=https://venture-bot.taskmind-ai.com`

> **SSE note:** Firebase rewrites stream `text/event-stream` responses without
> buffering (verified in `firebase-tools` `cloudRunProxy`), and the app sends a
> `ping` keepalive every 15 s  -- so the live debate feed works through Hosting.
> The only Hosting limit to be aware of is a 60 s request timeout, which does
> not apply to the EventSource stream (long-lived, keepalive-padded).

---

### Going public (from test  -> production)

Currently the app is in **test mode**  -- only emails in `VENTUREBOT_ALLOWED_EMAILS`
can log in. To open it to the public:

**1. Publish the OAuth consent screen:**

Go to `https://console.cloud.google.com/apis/credentials/consent?project=venturebot-506408`
 -> click **PUBLISH APP** (or "Go to verification" if Google requires it).

**Note:** Google may require app verification for the `email` and `profile`
scopes. This takes 1–3 days. For a hackathon: you can skip verification and
have up to 100 test users. Add each judge's email to `VENTUREBOT_ALLOWED_EMAILS`
as a workaround.

**2. Remove the allowlist (or keep it open):**

```bash
# Option A: Allow anyone with a Google account (remove allowlist)
gcloud run services update venturebot --region=europe-west3 \
  --update-env-vars="VENTUREBOT_ALLOWED_EMAILS="

# Option B: Freeze registrations but keep existing users
# (set VENTUREBOT_SIGNUP_CLOSED=true in env vars)
```

**3. Consider rate limiting before public launch:**

The app currently has no per-user rate limiting or queue. The design is
in `notes/PUBLIC_DEPLOYMENT_DESIGN.md`  -- implement at least the queue cap
and per-user budget before opening to the public to prevent one user from
burning the entire daily LLM budget.

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

**The system is complete and deployed.** Multi-user hardening P0s are done (see `notes/MULTIUSER_TASKS.md`).

| Milestone | Status |
|-----------|--------|
| M0.5 Safety baseline | ✅ kill switch, sandbox, budget, guards, auditor |
| M1 Core debate | ✅ ADK agents + autonomous orchestrator + HITL |
| M2 Observable UI | ✅ SSE, HITL buttons, idea history, exports |
| M3 Self-improvement | ✅ memory store, capture, review forks, dream review |
| Security hardening (multi-user P0) | ✅ workspace isolation, CSP, log redaction, OAuth+PKCE, server-side sessions, CSRF |
| Multi-user data plane (Phase B) | 🚧 tenancy keys in place; ownership checks, ephemeral debate engine, BYOK pending |
| GCP deployment (Cloud Run + CI/CD) | ✅ live at venturebot-442488405067.europe-west3.run.app; CI/CD on push to main |

---

## Documentation

- `PRD.md` - full product requirements
- `IMPLEMENTATION_PLAN.md` - build plan incl. which ADK samples each part is patterned on
- `SAFETY_REVIEW.md` - safety audit that gated all work
- `notes/GCP_DEPLOYMENT.md` - **complete GCP deployment guide** (setup script, Terraform, manual, costs, troubleshooting)
- `notes/MULTI_USER_DESIGN.md` - multi-user end-to-end design (local-first hybrid)
- `notes/MULTIUSER_SECURITY_REVIEW.md` - adversarial security review (findings + fix list)
- `notes/MULTIUSER_TASKS.md` - live multi-user task list + implementation log
- `notes/PUBLIC_DEPLOYMENT_DESIGN.md` - queue/rate-limit/BYOK mechanics
- `notes/LOOP_ARCHITECTURE_V2.md` - orchestrator loop design
- `CODE_REVIEW_FINAL.md`, `PLAN_REVIEW.md` - internal reviews
- `terraform/` - **infrastructure-as-code** (Terraform modules for all GCP resources)

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
