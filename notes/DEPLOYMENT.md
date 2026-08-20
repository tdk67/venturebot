# VentureBot — Deployment Guide

How to run VentureBot locally, on your VPS, and on GCP (hackathon target).

---

## 0. What's runnable right now

| Component | Status | How to run |
|-----------|--------|-----------|
| Dashboard (SSO + SSE + HITL + memory) | ✅ Working | `uvicorn` / Docker |
| Phase 1 debate (Researcher→…→PRD→Auditor) | ✅ Working | via dashboard or TestClient |
| Self-improvement (M3) | ✅ Working | `/api/memories`, `/scheduler/dream-review` |
| Phase 2 (blind TDD) | ❌ Not built | out of scope for hackathon (PRD §11 — post-hackathon) |

The whole thing is **one FastAPI app** (`src.dashboard:app`). Phase 1 agents
run **in-process** via `google-adk` — no separate Agent Engine needed for the demo.

---

## 1. Try it locally (no Docker)

```bash
cd ~/venturebot
source venv/bin/activate

# 1) Verify prerequisites
python -c "from google.adk.agents import LlmAgent; print('ADK OK')"
python -c "from src import config; assert config.google_api_key(); print('API key OK')"

# 2) Run the dashboard
uvicorn src.dashboard:app --host 0.0.0.0 --port 8080 --reload
```

Open **http://localhost:8080**:
1. Click **Sign in with Google** (allowlisted email only — see `.env`).
2. Type an idea → **▶ Debate**.
3. Watch Researcher → Advocate → Critic → Judge stream live.
4. Click **PROCEED ANYWAY** at the verdict gate.
5. Review the PRD + **Security audit badge** → **APPROVE**.

### Try it headless (no browser, still real LLM)

```bash
./venv/bin/python - <<'PY'
import asyncio
from src.agents.pipeline import run_debate
from src.steering import SteeringInbox

async def main():
    result = await run_debate("An app that tracks watering schedules for houseplants", inbox=SteeringInbox())
    print("status:", result.status)
    print("verdict:", result.verdict)
    print("prd (first 500):", (result.prd or "")[:500])
    print("security_audit:", result.security_audit)

asyncio.run(main())
PY
```

---

## 2. Run the tests (sanity before any deploy)

```bash
./venv/bin/python -m pytest tests/ -q
# expect: 84 passed
```

---

## 3. Deploy to your VPS (Docker)

```bash
cd ~/venturebot

# 3a) Build the image
docker build -t venturebot:latest .

# 3b) Prepare a writable data dir on the HOST (state.json + sqlite live here)
mkdir -p /opt/venturebot/data
#    ⚠️ the container runs as uid 10001; give it write access:
sudo chown -R 10001:10001 /opt/venturebot/data

# 3c) Run (secrets from .env, data persisted to the host volume)
docker run -d --name venturebot \
  -p 8080:8080 \
  --env-file .env \
  -v /opt/venturebot/data:/app/data \
  --restart unless-stopped \
  venturebot:latest

# 3d) Check
curl http://localhost:8080/api/auth/me
docker logs -f venturebot
```

> **HTTPS note:** behind nginx/caddy, set `VENTUREBOT_SECURE_COOKIES=1`
> (or the app auto-detects `https://` in the request). Without TLS, session
> cookies are sent in cleartext — fine for localhost, not for the open internet.
>
> **Google SSO note (per-domain, mandatory):** the `GOOGLE_CLIENT_ID` must have
> **this hostname listed under "Authorized JavaScript origins"** in the Google
> Cloud Console (APIs & Services → Credentials → the Web client). Without it,
> login fails with `The given origin is not allowed for the given client ID`.
> Add every domain you serve (e.g. `https://venturebot.taskmind-ai.com`). No
> redirect URI is needed — this app uses the JWT (ID-token) flow, not the code flow.

### Reverse proxy (nginx example)

```nginx
server {
    listen 443 ssl;
    server_name venturebot.example.com;
    # ... ssl_certificate / ssl_certificate_key ...

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;              # required for SSE
        proxy_read_timeout 3600s;         # keep SSE alive
    }
}
```

**SSE is critical** — you must set `proxy_buffering off` or the live debate
feed will buffer and never stream.

---

## 4. Deploy to GCP (hackathon)

### 4.1 Enable + auth

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com artifactregistry.googleapis.com
```

### 4.2 Build + push to Artifact Registry

```bash
gcloud artifacts repositories create venturebot --repository-format=docker \
  --location=us-central1 --description="VentureBot images"

gcloud auth configure-docker us-central1-docker.pkg.dev

docker tag venturebot:latest us-central1-docker.pkg.dev/YOUR_PROJECT_ID/venturebot/venturebot:latest
docker push us-central1-docker.pkg.dev/YOUR_PROJECT_ID/venturebot/venturebot:latest
```

### 4.3 Deploy to Cloud Run

```bash
gcloud run deploy venturebot \
  --image us-central1-docker.pkg.dev/YOUR_PROJECT_ID/venturebot/venturebot:latest \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars "GOOGLE_API_KEY=...","GOOGLE_CLIENT_ID=...","VENTUREBOT_ALLOWED_EMAILS=you@gmail.com","VENTUREBOT_SECURE_COOKIES=1" \
  --set-secrets "GOOGLE_API_KEY=gemini-api-key:latest"  # optional: Secret Manager
```

> **Secrets:** prefer Secret Manager over `--set-env-vars` for the API key.
> Cloud Run injects HTTPS automatically, so `VENTUREBOT_SECURE_COOKIES=1`.

### 4.4 Schedule the nightly dream-review

```bash
gcloud scheduler jobs create http venturebot-dream-review \
  --schedule="0 3 * * *" \
  --uri="https://YOUR_SERVICE.run.app/scheduler/dream-review" \
  --http-method=POST \
  --time-zone="UTC"
```

> **Auth note:** `POST /scheduler/dream-review` requires a signed session cookie
> (it's behind Google SSO). For the scheduler job, either (a) expose a separate
> service-account-authenticated endpoint, or (b) keep the cron *disabled* and
> trigger manually via the **"Run Dream Review"** button in the UI. For the
> hackathon demo, manual trigger is the simplest honest path — the button exists.

---

## 5. Go/no-go checklist before the demo (Task 23)

```bash
# 1) Tests
./venv/bin/python -m pytest tests/ -q              # 84 passed

# 2) LLM reachability (cheapest model)
./venv/bin/python -c "
import asyncio
from google.adk import Runner
from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.sessions import InMemorySessionService
from google.genai import types
from src import config
async def m():
    ss=InMemorySessionService(); sid=(await ss.create_session(app_name='v',user_id='u')).id
    r=Runner(agent=LlmAgent(name='x',model=Gemini(model=config.MODEL_RESEARCHER),instruction='Reply: OK',tools=[]),session_service=ss,app_name='v')
    o=''
    async for e in r.run_async(user_id='u',session_id=sid,new_message=types.Content(role='user',parts=[types.Part(text='ping')])):
        if e.content and e.content.parts and not e.partial: o=''.join(p.text for p in e.content.parts if getattr(p,'text',None))
    print('LLM:', repr(o))
asyncio.run(m())
"   # expect: LLM: 'OK'

# 3) google_search reachability (Critic needs it) — Gemini grounding, no GCP setup needed
./scripts/check_search.sh   # live ground-check: Researcher + Critic models

# 4) Pick a pre-tested demo idea that PROCEEDs; rehearse the full flow
```

---

## 6. Current deployment gaps (honest status)

| Gap | Impact | Fix |
|-----|--------|-----|
| ~~`google_search` needs Custom Search API enabled~~ | n/a — ADK's `google_search` is Gemini built-in search grounding, not the Custom Search JSON API | Nothing to enable; verify with `scripts/check_search.sh` |
| Phase 2 not built | Demo is Phase-1-only | PRD §11 (Build Plan) — post-hackathon |
| Scheduler auth for GCP cron | Nightly job can't use SSO cookie | Use manual button or add SA-auth endpoint |
| Data dir is a single volume | Not HA | Fine for single-instance demo |

**Bottom line:** you are at the "try it out" stage. Run locally via
`uvicorn` (section 1) or Docker (section 3), and the GCP path (section 4)
is ready to execute once you push the image (no Custom Search API needed — search runs on Gemini grounding via `GOOGLE_API_KEY`).
