# VentureBot

**A self-improving, multi-agent research & development system** built on Google ADK.  
Takes a vague idea → researches it → debates it (Advocate vs Critic vs Judge) → produces a scored PRD → builds a working MVP through blind TDD.

**Target:** Google Hackathon — Track 2: The Collaborative Partner

```
Vague Idea
  ↓
Research Agent (google_search) → Research Brief
  ↓
[Human Clarification if needed]
  ↓
Advocate → Critic → Judge (asymmetric info access)
  ↓
[Human: Proceed / Abort]
  ↓
PRD Writer → Structured PRD
  ↓
[Human: Approve / Changes / Reject]
  ↓
Phase 2: Blind TDD (PO → TestWriter → pytest → Coder → QA_PO)
  ↓
Working MVP
```

---

## Current Status (2026-08-19)

> **Phase 1 pipeline works end-to-end.** The 5-agent debate chain (Research → Advocate → Critic → Judge → PRD Writer) runs on Google ADK with Gemini models, includes kill switch, budget enforcement, HITL gates, and SSE streaming. **Dashboard UI is fully wired** (SSE + rendering + HITL buttons). **Self-improvement layer (M3) is built** (SQLite memory store, auto_capture, review_fork, dream_review, idea-tree pruning). **Phase 2 (blind TDD) is not built** (deliberately out of scope for the hackathon — see PRD §8.1).

### Milestone Progress

| Milestone | Progress | Status |
|-----------|----------|--------|
| **M0.5** Safety Baseline (S0-S10) | 80% | ✅ Kill switch, sandbox, budget, auth, input guard, XSS-safe. ⚠️ MCP config (S8) + Security Auditor (S10) missing |
| **M1** Phase 1 Core Debate | 85% | ✅ 5 ADK agents, custom pipeline, HITL gates, steering injection, URL ingestion |
| **M2** Observable UI | 90% | ✅ FastAPI + SSE + auth + JS rendering + HITL buttons (inline in `templates/index.html`) |
| **M3** Self-Improvement | 70% | ✅ SQLite store, idea tree + pruning, auto_capture, review_fork, dream_review + `/api/memories` + `/scheduler/dream-review`. ⚠️ Technique-library UI panel not wired |
| **M4** Shadow Mode + GCP Deploy | 0% | ❌ Not started |

### Component Status

| Component | File(s) | State |
|-----------|---------|-------|
| Phase 1 agents (Researcher, Advocate, Critic, Judge, PRD Writer) | `venturebot/agents/agents.py` | ✅ Working on ADK 2.7.1 |
| Pipeline orchestrator (resumable, kill-switch aware) | `venturebot/agents/pipeline.py` | ✅ Working |
| HITL gates (clarify, verdict, PRD approval) | `venturebot/agents/clarify.py`, pipeline.py | ✅ Working |
| Steering inbox (mid-run guidance injection) | `venturebot/steering.py` | ✅ Working |
| Kill switch (StopEvent + process group kill) | `venturebot/run_manager.py` | ✅ Working |
| Output guard (AST check + hardcoded secret scan) | `venturebot/guard.py` | ✅ Working |
| Input guard (injection detection + quarantine) | `venturebot/input_guard.py` | ✅ Working |
| Sandbox (unprivileged UID + network isolation + rlimits) | `venturebot/sandbox.py` | ✅ Working |
| Budget enforcement (pre-call check + persistent tracking) | `venturebot/budget.py` | ✅ Working |
| Google SSO auth (allowlist + signed cookies) | `venturebot/auth.py` | ✅ Working |
| FastAPI dashboard (SSE + all API endpoints) | `venturebot/dashboard.py` | ✅ API working |
| Dashboard HTML/CSS | `templates/index.html` | ✅ Full SPA: SSO, SSE feed, steering, verdict/PRD HITL buttons |
| State store (JSON file-backed) | `venturebot/store.py` | ✅ Working |
| URL fetcher (research material ingestion) | `venturebot/url_fetch.py` | ✅ Working |
| Gemini usage tracker | `venturebot/gemini_usage.py` | ✅ Working |
| Phase 2 agents (PO, TestWriter, Coder, QA_PO) | — | ❌ Wiped (safety review), not rebuilt |
| Self-improvement layer (auto_capture, review_fork, dream_review) | `venturebot/memory/` | ✅ Built (M3) |
| Idea tree with pruning | `venturebot/memory/idea_tree.py` | ✅ Built + deterministic pruning rules |
| Bridge (Phase 1 → Phase 2 handoff) | — | ❌ Not started |
| GCP deployment (Agent Engine + Cloud Run) | — | ❌ Not started |

---

## Architecture

```
/root/venturebot/
├── PRD.md                          ← product requirements (VB-PRD-2026-08-18)
├── IMPLEMENTATION_PLAN.md          ← detailed build plan + task breakdown
├── SAFETY_REVIEW.md                ← safety audit that gated all work
├── Review-PRD.md                   ← hackathon alignment + demo strategy
├── README.md                       ← you are here
├── .env                            ← live secrets (NEVER committed)
├── .env.example                    ← env var template
├── .gitignore                      ← excludes .env, state.json, data/, venv/
├── state.json                      ← current pipeline state (resumable)
│
├── venturebot/                     ← Python package
│   ├── __init__.py
│   ├── config.py                   ← env-driven config (models, budgets, paths)
│   ├── dashboard.py                ← FastAPI app: SSE, auth, HITL, all /api/* endpoints
│   ├── store.py                    ← JSON file-backed state (single source of truth)
│   ├── auth.py                     ← Google SSO (jwt verification + allowlist)
│   ├── budget.py                   ← cumulative LLM spend enforcement
│   ├── run_manager.py              ← kill switch (StopEvent + process group kill)
│   ├── guard.py                    ← post-LLM output guard (AST check + secret scan)
│   ├── input_guard.py              ← pre-LLM injection guard + quarantine convention
│   ├── sandbox.py                  ← pytest isolation (unshare, setuid, rlimits)
│   ├── steering.py                 ← user guidance inbox (drained at checkpoints)
│   ├── url_fetch.py                ← fetches user-provided URLs for research material
│   ├── gemini_usage.py             ← Gemini token/cost tracker
│   ├── llm_client.py               ← legacy OpenRouter client (kept for reference)
│   │
│   └── agents/                     ← Phase 1 ADK agents
│       ├── __init__.py
│       ├── agents.py               ← 5 LlmAgent definitions (Researcher→PRD Writer)
│       ├── pipeline.py             ← custom orchestrator (resumable, kill-switch aware)
│       ├── prompts.py              ← system prompts for all 5 agents
│       ├── schemas.py              ← Pydantic output schemas (ResearchBrief, JudgeVerdict)
│       ├── clarify.py              ← HITL clarification tool (LongRunningFunctionTool)
│       └── (pipeline wires auto_capture into memory/)
│
│   └── memory/                     ← self-improvement layer (M3)
│       ├── __init__.py
│       ├── sqlite_store.py         ← SQLite store (facts, lessons, techniques, profile, idea tree)
│       ├── idea_tree.py            ← deterministic pruning rules (PRD §5.5)
│       ├── auto_capture.py         ← Fork 1: persist session facts (throttled)
│       ├── review_fork.py          ← Fork 2: fire-and-forget LLM turn analysis
│       ├── dream_review.py         ← Fork 3: nightly consolidation + pruning
│       └── _throttle.py            ← 120s per-session fork cooldown
│
├── templates/
│   └── index.html                  ← dashboard SPA (SSO, SSE, HITL buttons, XSS-safe rendering)
│
├── tests/                          ← pytest test suite (64 tests)
│   ├── test_safety.py              ← guard, input_guard, sandbox, budget, kill switch
│   ├── test_dashboard.py           ← API endpoint auth + status codes
│   ├── test_pipeline.py            ← verdict parsing, debate flow (mocked)
│   ├── test_steering.py            ← steering inbox + concurrency
│   ├── test_auth.py                ← SSO verification + session tokens
│   ├── test_url_fetch.py           ← URL validation + fetching
│   └── test_memory.py              ← memory store CRUD, pruning rules, throttle
│
├── data/
│   ├── budget.json                 ← daily spend limit config
│   └── gemini_usage.json           ← cumulative LLM cost log
│
├── scripts/
│   ├── secret_scan.sh              ← git pre-push secret scanner
│   └── check_gemini_credits.sh     ← Gemini API quota checker
│
├── venv/                           ← Python 3.11 virtualenv (google-adk, fastapi, etc.)
└── .git/                           ← git repo (pre-push hook runs secret_scan.sh)
```

---

## Key Design Decisions

### 1. Custom Orchestrator over SequentialAgent
The pipeline uses a custom orchestrator (`pipeline.py`) instead of ADK's `SequentialAgent` because:
- SequentialAgent is deprecated in ADK 2.7
- SequentialAgent runs all sub-agents unconditionally (no conditional verdict gate)
- Custom orchestrator polls the kill switch between agents
- Steering messages are injected at checkpoints (between agents), never mid-turn
- Pipeline is resumable: paused state survives across human decisions

### 2. Asymmetric Information Access (Blind Debate)
- **Advocate** has NO tools — argues only from the research brief
- **Critic** HAS `google_search` — can fact-check with live web evidence
- This eliminates single-model confirmation bias

### 3. Fail Loud, Fail Honest
No silent fallbacks. When something fails:
1. Stop (don't continue degraded)
2. Report (full context: component, error type, what was attempted)
3. Preserve state (save what was computed)
4. Suggest fix (if known)

### 4. Three-Layer Defense
1. **Pre-LLM** (`input_guard.py`) — injection detection + quarantine convention
2. **Post-LLM** (`guard.py`) — AST check (banned constructs) + hardcoded secret scan
3. **Runtime** (`sandbox.py`) — unprivileged UID, network isolation, rlimits, filesystem restrictions

---

## Quick Start

### Prerequisites

```bash
cd /root/venturebot
source venv/bin/activate

# Verify ADK is installed
python -c "from google.adk.agents import LlmAgent; print('ADK OK')"

# Verify Gemini API key
python -c "from google.adk.models import Gemini; m = Gemini(model='gemini-2.0-flash'); print('Gemini OK')"
```

### Run the Dashboard

```bash
cd /root/venturebot
uvicorn venturebot.dashboard:app --host 0.0.0.0 --port 8080
```

Open http://localhost:8080 — the dashboard will show:
- ✅ API endpoints are live (check `/api/state`, `/api/auth/me`)
- ⚠️ UI rendering is incomplete (SSE events stream but aren't rendered yet)

### API Endpoints

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/` | Dashboard HTML | No (static) |
| GET | `/api/auth/client-id` | Google OAuth client ID | No |
| GET | `/api/auth/me` | Current user (or `authenticated: false`) | No |
| POST | `/api/auth/google` | Verify Google credential, set session cookie | No |
| POST | `/api/auth/logout` | Clear session cookie | No |
| GET | `/api/state` | Full pipeline state + budget | ✅ |
| POST | `/api/reset` | Reset state to initial | ✅ |
| POST | `/api/stop` | Cancel active run (kill switch) | ✅ |
| POST | `/api/run-phase1` | Start debate with `{"idea": "..."}` | ✅ |
| POST | `/api/steering` | Queue guidance for next checkpoint | ✅ |
| GET | `/api/steering` | Current steering inbox state | ✅ |
| POST | `/api/resume` | Resume paused debate with decision | ✅ |
| GET | `/api/paused` | List of paused run IDs | ✅ |
| GET | `/api/events` | SSE stream of pipeline events | ✅ |
| POST | `/api/budget/raise` | Raise daily budget limit | ✅ |

### Run Tests

```bash
cd /root/venturebot
pytest tests/ -v
```

Current test coverage: ~25% (safety tests are solid; most modules undertested)

---

## Environment Variables

```bash
# Required
GEMINI_API_KEY=AIza...                    # Google AI Studio API key

# Phase 1 models (all configurable)
VENTUREBOT_MODEL_RESEARCHER=gemini-2.5-flash
VENTUREBOT_MODEL_ADVOCATE=gemini-2.5-flash
VENTUREBOT_MODEL_CRITIC=gemini-2.5-pro
VENTUREBOT_MODEL_JUDGE=gemini-2.5-pro
VENTUREBOT_MODEL_PRD_WRITER=gemini-2.5-pro

# Google SSO (for dashboard auth)
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
VENTUREBOT_ALLOWED_EMAILS=you@gmail.com

# Budget
VENTUREBOT_DAILY_BUDGET_LIMIT=20.0        # USD, default $20

# Sandbox
VENTUREBOT_SANDBOX_USER=nobody
VENTUREBOT_SANDBOX_GROUP=nogroup
```

See `.env.example` for the full template.

---

## What's Next (Priority Order)

### 🔴 P0 — Before Demo

- [x] **Write dashboard JavaScript** — SSE + rendering + HITL buttons (done, inline in `templates/index.html`)
- [x] **Remove dead Phase 2 code** — `guard.py` is clean
- [x] **Add critical tests** — budget, auth, kill switch, input guard, sandbox isolation
- [x] **Fix verdict parser** — now fails loud (raises `ValueError`) instead of silently PARKing
- [ ] **Wire technique-library UI panel** — surface `/api/memories` in the dashboard right panel (last M3 UI piece)
- [ ] **Add scheduled dream-review** — wire APScheduler/cron to hit `/scheduler/dream-review` nightly

### 🟡 P1 — After Demo

- [ ] **Persist paused sessions** — `_SESSIONS` metadata is saved to `data/paused_sessions.json`; full session objects (ADK session_service) are runtime-only
- [ ] **Add Security Auditor agent** (S10) — proof-read PRDs for hallucinations + missing NFRs
- [ ] **Wire MCP tool config** (S8) — make `google_search` and messaging channels config-driven
- [ ] **Add rate limiting** — Prevent API abuse (slowapi or similar)
- [ ] **Add review_fork firing to pipeline** — `analyze_turn` is built + tested; wire it as a fire-and-forget task after each agent turn

### 🟢 P2 — Post-Hackathon

- [ ] **Build Phase 2** — Rebuild blind TDD loop on ADK architecture (PO → TestWriter → Coder → QA_PO)
- [ ] **Bridge Phase 1 → Phase 2** — Pass approved PRD from debate to TDD harness
- [ ] **Shadow mode** — Run ADK coder alongside custom coder, compare metrics
- [ ] **Anti-degradation gate** — Auto-revert if ADK pass rate drops below 80%
- [ ] **GCP deployment** — Agent Engine + Cloud Run + Memory Bank
- [ ] **Multi-provider fallback** — Add OpenRouter as tier 2 if Gemini is unavailable
- [ ] **Per-run workspace** — Isolate artifacts under `runs/<run_id>/`

---

## Known Issues

1. **Phase 2 is missing** — The old Phase 2 code (OpenRouter blind TDD loop) was correctly wiped per SAFETY_REVIEW.md. Deliberately out of scope for the hackathon (PRD §8.1); the dashboard has no Kanban panel for it.

2. **review_fork not yet fired** — `analyze_turn` is implemented + unit-tested, but the pipeline only runs auto_capture (Fork 1). Fork 2 (LLM analysis) needs a fire-and-forget wiring at the checkpoint level.

3. **Dream review is manual-only** — The endpoint works (`POST /scheduler/dream-review`) but there's no cron/APScheduler trigger yet.

4. **Paused sessions are in-memory** — `_SESSIONS` holds ADK session objects (runtime-only); metadata is persisted to `data/paused_sessions.json` for observability, but a full resume across restart is not supported.

5. **Test coverage improved but still partial** — 64 tests. Safety-critical paths (budget, auth, kill switch, sandbox) now have coverage; the ADK agent logic itself is still lightly tested (mocked at the Runner boundary).

---

## Documentation

- **PRD.md** — Full product requirements (1268 lines)
- **IMPLEMENTATION_PLAN.md** — Detailed task breakdown with estimates (1473 lines)
- **SAFETY_REVIEW.md** — Safety audit that gated all work
- **Review-PRD.md** — Hackathon alignment, uniqueness analysis, demo strategy
- **PLAN_REVIEW.md** — Plan critique and risk mitigation
- **CODE_REVIEW_FINAL.md** — Principal engineer code review (2026-08-19)
- **idea-01.md, idea-02.md** — Original concept documents
- **GEMINI_CREDITS.md** — Gemini API quota and cost tracking

---

## License

Private — hackathon submission.

---

## Contact

Built for the Google ADK Hackathon 2026. Track 2: The Collaborative Partner.
