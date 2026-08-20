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

> **Phase 1 pipeline works end-to-end.** The 5-agent debate chain (Research → Advocate → Critic → Judge → PRD Writer) runs on Google ADK with Gemini models, includes kill switch, budget enforcement, HITL gates, and SSE streaming. **Dashboard UI is fully wired** (SSE + rendering + HITL buttons + idea-history timeline). **Self-improvement layer (M3) is built** (SQLite memory store, auto_capture, review_fork, dream_review, idea-tree pruning). **Idea history + crash-safe checkpoint persistence is built** — ideas archive with PRD/transcript/scores, and mid-run checkpoints survive restarts via `resume_from_checkpoint`. **Phase 2 (blind TDD) is not built** (deliberately out of scope for the hackathon — see PRD §11 Build Plan, post-hackathon).

### Milestone Progress

| Milestone | Progress | Status |
|-----------|----------|--------|
| **M0.5** Safety Baseline (S0-S10) | 90% | ✅ Kill switch, sandbox, budget, auth, input guard, XSS-safe, Security Auditor (S10) + proof-read gate. ⚠️ MCP config (S8) missing |
| **M1** Phase 1 Core Debate | 85% | ✅ 5 ADK agents, custom pipeline, HITL gates, steering injection, URL ingestion |
| **M2** Observable UI | 95% | ✅ FastAPI + SSE + auth + JS rendering + HITL buttons + idea-history timeline with sidebar facets (status/tag/date), search, PRD viewer, pagination, CSV export |
| **M3** Self-Improvement | 90% | ✅ SQLite store, idea tree + pruning, auto_capture, review_fork (wired fire-and-forget), dream_review, scheduler, `/api/memories` + technique-library UI panel |
| **M4** Shadow Mode + GCP Deploy | 0% | ❌ Not started |

### Component Status

| Component | File(s) | State |
|-----------|---------|-------|
| Phase 1 agents (Researcher, Advocate, Critic, Creative, Judge, PRD Writer) | `src/agents/agents.py` | ✅ Working on ADK 2.7.1 |
| Pipeline orchestrator (resumable, kill-switch aware) | `src/agents/pipeline.py` | ✅ Working |
| HITL gates (clarify, verdict, PRD approval) | `src/agents/clarify.py`, pipeline.py | ✅ Working |
| Steering inbox (mid-run guidance injection) | `src/steering.py` | ✅ Working |
| Kill switch (StopEvent + process group kill) | `src/run_manager.py` | ✅ Working |
| Output guard (AST check + hardcoded secret scan) | `src/guard.py` | ✅ Working |
| Artifact scanner + proof-read gate (S10) | `src/artifact_scanner.py` | ✅ Working — secret + injection + AST scans, never auto-passes |
| Security Auditor agent (S10) | `src/agents/agents.py` (`auditor`) | ✅ Working — proofs PRD before approval |
| Input guard (injection detection + quarantine) | `src/input_guard.py` | ✅ Working |
| Sandbox (unprivileged UID + network isolation + rlimits) | `src/sandbox.py` | ✅ Working |
| Budget enforcement (pre-call check + persistent tracking) | `src/budget.py` | ✅ Working |
| Google SSO auth (allowlist + signed cookies) | `src/auth.py` | ✅ Working |
| FastAPI dashboard (SSE + all API endpoints) | `src/dashboard.py` | ✅ API working |
| Dashboard HTML/CSS | `templates/index.html` | ✅ Full SPA: SSO, SSE feed, steering, verdict/PRD HITL buttons |
| State store (JSON file-backed) | `src/store.py` | ✅ Working |
| URL fetcher (research material ingestion) | `src/url_fetch.py` | ✅ Working |
| Gemini usage tracker | `src/gemini_usage.py` | ✅ Working |
| Checkpoint persistence (crash-safe resume) | `src/agents/pipeline.py` | ✅ Working — atomic per-agent snapshots in `data/checkpoints/`, archived on completion |
| Idea archive (PRD/transcript/scores in SQLite) | `src/memory/sqlite_store.py` | ✅ Working — `idea_tree` populated by pipeline, queried by `/api/ideas*` |
| Tag extraction (portfolio-style categories) | `src/memory/tagging.py` | ✅ Working — keyword-based |
| Phase 2 agents (PO, TestWriter, Coder, QA_PO) | — | ❌ Wiped (safety review), not rebuilt |
| Self-improvement layer (auto_capture, review_fork, dream_review) | `src/memory/` | ✅ Built (M3) |
| Idea tree with pruning | `src/memory/idea_tree.py` | ✅ Built + deterministic pruning rules |
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
├── src/                            ← Python package (renamed from venturebot/venturebot)
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
│   ├── artifact_scanner.py         ← S10 deterministic scanner + proof-read gate
│   ├── scheduler.py                ← nightly dream-review cron (APScheduler)
│   ├── llm_client.py               ← legacy OpenRouter client (kept for reference)
│   │
│   └── agents/                     ← Phase 1 ADK agents
│       ├── __init__.py
│       ├── agents.py               ← 6 LlmAgent definitions (Researcher→PRD Writer→Auditor)
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
├── tests/                          ← pytest test suite (114 tests)
│   ├── test_safety.py              ← guard, input_guard, sandbox, budget, kill switch
│   ├── test_dashboard.py           ← API endpoint auth + status codes
│   ├── test_pipeline.py            ← verdict parsing, debate flow (mocked)
│   ├── test_steering.py            ← steering inbox + concurrency
│   ├── test_auth.py                ← SSO verification + session tokens
│   ├── test_url_fetch.py           ← URL validation + fetching
│   ├── test_memory.py              ← memory store CRUD, pruning rules, throttle
│   ├── test_review_fork.py         ← review_fork analysis, scheduler
│   ├── test_artifact_scanner.py    ← S10 scanner + proof-read gate + audit parsing
│   ├── test_checkpoint.py          ← checkpoint atomicity, resume, archive move
│   ├── test_ideas_store.py         ← update_idea_content, partial/idempotent, tags
│   └── test_ideas_api.py           ← /api/ideas* + checkpoints + facets + CSV
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
uvicorn src.dashboard:app --host 0.0.0.0 --port 8080
```

Open http://localhost:8080 — the dashboard will show:
- ✅ API endpoints are live (check `/api/state`, `/api/auth/me`)
- ✅ Full debate pipeline: Researcher → Advocate → Critic → Creative → Judge → PRD

### Stub Server (UI tuning, zero LLM cost)

```bash
cd /root/venturebot
./venv/bin/uvicorn src.stub_server:app --host 0.0.0.0 --port 8091
```

Scripted in-memory debate that auto-advances through all phases on a timer.
Identical UI (same dashboard routes, auth, ideas, SSE) but every LLM call is
replaced with canned data. Use this to iterate on UI changes without spending tokens.

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
| GET | `/api/ideas` | Paginated idea list (filters: search, status, category, date) | ✅ |
| GET | `/api/ideas/facets` | Sidebar facets (tag/date/status counts) | ✅ |
| GET | `/api/ideas/csv` | CSV export of filtered ideas | ✅ |
| GET | `/api/ideas/{id}` | Full idea detail (PRD, transcript, scores) | ✅ |
| POST | `/api/ideas/{id}/resume` | Load an idea as active debate context | ✅ |
| POST | `/api/ideas/{id}/archive` | Park an idea | ✅ |
| GET | `/api/checkpoints` | List in-progress (resumable) checkpointed runs | ✅ |
| POST | `/api/checkpoints/{id}/resume` | Resume a checkpointed debate after restart | ✅ |

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
- [x] **Wire technique-library UI panel** — surface `/api/memories` in the dashboard right panel (last M3 UI piece)
- [x] **Add scheduled dream-review** — wire APScheduler/cron to hit `/scheduler/dream-review` nightly
- [x] **Add Security Auditor agent** (S10) — proof-read PRDs for hallucinations + missing NFRs
- [ ] **Wire MCP tool config** (S8) — make `google_search` and messaging channels config-driven
- [ ] **Add rate limiting** — Prevent API abuse (slowapi or similar)
- [x] **Add review_fork firing to pipeline** — `analyze_turn` is wired as a fire-and-forget task after each agent turn

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

1. **Phase 2 is missing** — The old Phase 2 code (OpenRouter blind TDD loop) was correctly wiped per SAFETY_REVIEW.md. Deliberately out of scope for the hackathon (PRD §11 Build Plan, post-hackathon); the dashboard has no Kanban panel for it.

2. **Dream review is manual-only by default** — The endpoint works (`POST /scheduler/dream-review`) and the APScheduler cron is wired, but the scheduler is off unless `VENTUREBOT_ENABLE_SCHEDULER=1` is set.

3. **Paused sessions are in-memory** — `_SESSIONS` holds ADK session objects (runtime-only) for the *verdict/PRD gate resume* path. The **checkpoint layer** (`data/checkpoints/<run_id>.json`) now covers crash/restart recovery: `resume_from_checkpoint` reconstructs `DebateResult` from disk and re-runs only the remaining phases, so mid-run data loss is solved even though the ADK session history itself is not persisted.

4. **Test coverage is solid but ADK agents lightly tested** — 114 tests. Safety-critical paths (budget, auth, kill switch, sandbox), the memory layer, and idea-history/checkpoint persistence have coverage; the ADK agent logic itself is mocked at the Runner boundary.

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
