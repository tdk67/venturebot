# Idea Lint

**An adversarial, multi-agent venture validation engine** built on [Google ADK](https://github.com/google/adk-python).
Takes a raw startup concept $\rightarrow$ researches it with live Google Search grounding $\rightarrow$ debates it across specialized adversarial agents (Advocate vs Critic vs Creative vs Judge) $\rightarrow$ outputs an audit-proof Product Requirements Document (PRD) with human-in-the-loop gates at every critical decision.

```
Startup Concept
  │
Orchestrator (Autonomous ADK Agent Loop)
  ├─ Researcher (Google Search Grounding) → Research Brief
  ├─ Creative Ideator (Divergent angles, lateral pivots)
  ├─ Advocate → Critic → Judge (Asymmetric information courtroom)
  │    └─ [Clarification & Decision Gate — Durable Pause if parked or ambiguous]
  │
PRD Writer → Structured PRD (Machine-actionable specifications)
  │
Security Auditor (Deterministic Quality Gate: Guardrails, privacy, NFRs)
  │
Scored PRD + Verdict Scorecard + Full Debate Transcript
```

---

## What It Is

Idea Lint is an **impartial courtroom for startup ideas**. Instead of a single "yes-man" LLM validating your ideas with generic praise, a panel of specialized agents with *deliberately asymmetric information access* stress-tests it:

- **Researcher**: Mines competitor signals, market metrics, and technical benchmarks via live Google Search grounding.
- **Advocate**: Argues the strongest possible bull case using only positive research evidence.
- **Critic**: Red-teams technical feasibility, uncovers hidden moats, unit economics traps, and market saturation.
- **Creative Ideator**: Runs hot to propose lateral pivot angles and unconventional market wedges.
- **Judge**: Synthesizes the debate into structured scores (**Novelty**, **Feasibility**, **Market Fit**) and a definitive verdict (**PROCEED / PARK / PRUNE**).
- **PRD Writer & Security Auditor**: Produces a rigorous PRD and audits it for architectural risks, secret leaks, and edge cases.

---

## Privacy-First & Bring-Your-Own-Key (BYOK) Architecture

Idea Lint is designed from the ground up to respect founder privacy:

1. **Zero Cloud Storage & Stateless Backend**:
   - The backend holds **no database** of user ideas, transcripts, or credentials.
   - All debate history, scored runs, and generated PRDs live **exclusively on your local device** in your browser's IndexedDB.
   - Data can be exported at any time to **JSON, CSV, Markdown, or PDF**.

2. **100% Free Tier API Key Compatible ($0 Cost)**:
   - Powered by Google Gemini (`AIza...`) from [Google AI Studio](https://aistudio.google.com/app/apikey).
   - **No credit card required** — Google AI Studio's free tier provides 15 Requests Per Minute (RPM) and 1,500 Requests Per Day (RPD), plenty for multi-agent debates.
   - OpenRouter keys (`sk-or-v1-...`) are also supported.
   - Built-in **exponential backoff retry handling** gracefully absorbs transient rate-limits (`429`) without failing your run.

3. **In-Memory Ephemeral Execution**:
   - Your API key is stored securely in your browser and sent only in-memory to execute the live debate run.
   - The server immediately discards and scrubs the key upon run completion.

---

## Quick Start (Run Locally)

### 1. Prerequisites
- **Python 3.12+**
- **Node.js 18+**
- A free **Google Gemini API Key** from [Google AI Studio](https://aistudio.google.com/app/apikey)

### 2. Installation

#### Clone and Set Up Python Virtual Environment
```bash
# Clone the repository
git clone https://github.com/tdk67/venturebot.git
cd venturebot

# Create and activate virtual environment
python -m venv .venv

# On Linux/macOS:
source .venv/bin/activate
pip install -r requirements.txt

# On Windows (PowerShell):
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

#### Build TypeScript Frontend SPA
```bash
cd frontend
npm install
npm run build
cd ..
```

### 3. Launch the Server

```bash
# On Linux/macOS:
python -m uvicorn src.dashboard:app --host 127.0.0.1 --port 8090

# On Windows (PowerShell):
.venv\Scripts\python.exe -m uvicorn src.dashboard:app --host 127.0.0.1 --port 8090
```

### 4. Open the App
- Open your browser to **`http://127.0.0.1:8090/app`** (or **`http://127.0.0.1:8090/`** for the Landing Page).
- Click **🔑 Set API Key** in the top navigation bar.
- Paste your Google Gemini API key (`AIza...`) and click **Validate & Save**.
- Enter your startup concept and click **🚀 Start Debate**!

---

## Using Idea Lint

1. **Submit an Idea**: Type your startup concept into the prompt box (optionally include competitor or reference URLs).
2. **Watch the Debate Live**: Real-time Server-Sent Events (SSE) stream every agent turn, duration timer, and token metric.
3. **Handle Clarification & Pivot Gates**: If an idea hits a blocker or receives a `PARK` verdict, the Orchestrator durable pause engages:
   - Review the Judge's findings and proposed pivot points.
   - Click quick-action presets (`✅ [Approve]`, `✏️ [Changes]`, `🛑 [Reject]`) or type custom direction.
4. **Inspect Generated Artifacts**:
   - **Verdict Scorecard**: Detailed 1–10 scores for Novelty, Feasibility, and Market Viability.
   - **Research Brief**: Grounded market research citations and search findings.
   - **Structured PRD**: Functional requirements, user stories, data schemas, and non-functional requirements.
   - **Security Audit**: Security posture and technical risk mitigation check.
5. **Manage & Export History**:
   - Filter and search past ideas by status (`ALL`, `PROCEED`, `PARK`, `PRUNE`).
   - Export full debate reports as **Markdown (`.md`)** or formatted **PDF**.
   - Backup or restore your entire database with one-click **JSON** / **CSV** import & export.
   - Re-run or resume any past debate with new guidance at any time.

---

## Backend API Contract

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | `GET` | Server liveness check |
| `/api/debates` | `POST` | Create a debate run (accepts `idea`, `api_key`, `urls`, `comment`) |
| `/api/debates/{id}` | `GET` | Get current execution status |
| `/api/debates/{id}/events` | `GET` | Per-run SSE event stream |
| `/api/debates/{id}/result` | `GET` | Fetch final structured result payload (`200` ready / `202` in-flight / `410` swept) |
| `/api/debates/{id}/result/ack` | `POST` | Client acknowledges result receipt (initiates cleanup) |
| `/api/debates/{id}/clarify` | `POST` | Submit founder answer to a durable clarification pause |
| `/api/byok/verify` | `POST` | Validate BYOK key format (`AIza...` / `sk-or-v1-...`) |

---

## Testing & Verification

The test suite covers the complete API contract, ADK agent workflows, rate-limiting, and error propagation:

```bash
# Run the full test suite (199 tests)
.venv\Scripts\pytest -q
```

---

## Project Status & Roadmap

### Implemented & Verified
- [x] **T1 — Near-Stateless API Skeleton**: Clean REST API contract with ephemeral run management.
- [x] **T2 — Autonomous Orchestrator**: Multi-agent delegation, loud error propagation, and durable clarification pauses.
- [x] **T3 — BYOK Plumbing**: In-memory ephemeral API key propagation with automated key scrub and redaction.
- [x] **T4 — Rate Limits & Caps**: Per-IP anti-flood limits, active execution concurrency guards, and body size limits.
- [x] **T5 — Ephemeral Store**: TTL sweeper with ACK-based lifecycle management.
- [x] **T6 — Client-Side Store (IndexedDB)**: Local-first schema migration, multi-run history, and JSON/CSV/Markdown backup.
- [x] **T7 — Live Debate View & Scorecard**: Real-time SSE transcript, agent progress chips, and Judge score normalization.
- [x] **T8 — Free Tier BYOK UX**: Client-side key validation with persistent local storage and automated retry backoff.

### Planned / Upcoming (Post-Hackathon)
- [ ] **T9 — Disconnect Recovery**: Automatic client-side reconnect with `Last-Event-ID` resume.
- [ ] **T10 — Multi-Agent Sandbox Test Suite**: Extended port scanner and isolation test harness.
- [ ] **T11 — Production Landing Finalization**: Interactive demo mode.
- [ ] **T12 — Cloud Run Deployment**: Keyless Workload Identity deployment.
- [ ] **Self-Improvement Layer (Client-Side)**: Local-first lesson capture and preference learning without server-side storage.

---

## Tech Stack

- **Agent Engine**: [Google ADK](https://github.com/google/adk-python) (`LlmAgent`, `FunctionTool`, `SessionService`)
- **Models**: Google Gemini 2.0 Flash (`gemini-2.0-flash`), Gemini 1.5 Pro
- **Backend**: FastAPI, Uvicorn, Python 3.12+
- **Frontend**: Vanilla TypeScript, Tailwind CSS, esbuild, IndexedDB
- **Export Engine**: Markdown, DOMPurify, Native Print-to-PDF
