# Idea History with Checkpointing — Implementation Addendum v2

**Date:** 2026-08-19  
**Replaces:** IDEA_HISTORY_ADDENDUM.md (v1 — incomplete persistence model)  
**Status:** Design — adds checkpoint-aware persistence + portfolio-style UI  
**Priority:** P1 — user experience differentiator

## Problem (Re-stated)

VentureBot has no crash-safe persistence. Two failure modes exist today:

1. **Between-run amnesia** — each `start_run()` wipes `state.json`. Past ideas,
   PRDs, and debate transcripts are not retrievable from the dashboard.

2. **Mid-run data loss** — `_SESSIONS` holds paused debates **in memory only**.
   The code admits it: _"Warning: {len} paused sessions lost on restart
   (in-memory sessions cannot persist)"_. If the server restarts during a
   debate, the partial research brief/argument/rebuttal is gone — the human
   must start over.

The `idea_tree` table has the right schema to fix both, but the pipeline
never writes its intermediate outputs there.

## Design: Two-Layer Persistence

### Layer 1: Checkpoint persistence (solves crash/recovery)

After each agent turn, serialize `DebateResult` to `data/checkpoints/<run_id>.json`.
This file is the authoritative resume-snapshot. On crash + restart, the
dashboard detects it and offers "[Continue where you left off]".

**Checkpoint structure:**
```json
{
  "idea": "A CLI tool for git diffs",
  "run_id": "a925b0b83b03",
  "current_phase": "critic",               // next agent to run
  "iteration": 0,
  "research_brief": "...full text...",       // already computed
  "advocate_argument": "...full text...",    // already computed
  "critic_rebuttal": null,                  // not yet run
  "verdict_text": null,
  "verdict": null,
  "prd": null,
  "security_audit": null,
  "messages_snapshot": [...],                // last 20 store.log() calls
  "saved_at": 1724086461.123
}
```

**Checkpoint triggers** (in `_run_agent`, after each agent completes):

| After agent | Phase value in checkpoint |
|------------|--------------------------|
| Researcher  | `advocate` |
| Advocate    | `critic` |
| Critic      | `judge` |
| Judge       | `verdict` |
| PRD Writer  | `auditor` |
| Auditor     | `needs_approval` |

The checkpoint is **atomic** (write to tempfile + rename) — same pattern as
`store.py::save_state()`. A corrupted checkpoint (half-written) never exists.

### Layer 2: Idea archive (solves between-run amnesia)

When a debate reaches `done` or `stopped` (human approved/aborted), the
checkpoint is finalized and saved into the `idea_tree` table as an
immutable archive record. The checkpoint file is moved to
`data/archives/<run_id>.json`.

**Key insight:** The idea_tree table already has columns for `research_brief`,
`debate_transcript`, `prd_text`, `scores`, `workspace_path`. The gap is that
the pipeline never writes to them. The checkpoint mini-snapshot feeds them:

```python
store = get_store()
store.update_idea_content(
    idea_id,
    research_brief=result.research_brief,
    debate_transcript=json.dumps(result.events),  # serialized list
    prd_text=result.prd,
    workspace_path=f"runs/{run_id}/",
)
store.update_idea_scores(idea_id, result.verdict.get("scores", {}))
```

### Resume flow

```
Server restarts → dashboard loads →
  GET /api/checkpoints finds data/checkpoints/*.json →
  UI shows "You have 1 in-progress debate" →
  [Continue] →
    POST /api/checkpoints/{run_id}/resume →
      pipeline reads checkpoint.json →
      skips already-completed phases →
      resumes from current_phase →
      reuses stored research_brief/argument/rebuttal →
      sends "continue from where you left off" context to next agent
```

The `InMemorySessionService` is NOT persisted — but that's fine because
our orchestrator passes accumulated text into each agent's prompt (line ~238:
`f"Research Brief:\n\n{brief}\n\nArgue FOR this idea."`). Each agent gets
a fresh context window from the serialized DebateResult — we don't need
the ADK session history to resume.

## API Design

### New endpoints

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/ideas` | List all ideas from idea_tree (paginated, filterable) | ✅ |
| GET | `/api/ideas/{id}` | Full idea detail (PRD, transcript, scores, links) | ✅ |
| POST | `/api/ideas/{id}/resume` | Load an idea as the active debate context | ✅ |
| POST | `/api/ideas/{id}/archive` | Set idea status to PARK | ✅ |
| GET | `/api/checkpoints` | List in-progress checkpointed runs | ✅ |
| POST | `/api/checkpoints/{run_id}/resume` | Resume a checkpointed debate | ✅ |

### GET /api/ideas response shape (matches portfolio Project type)

```json
{
  "items": [
    {
      "id": "abc123...",
      "title": "A CLI tool that summarizes git diffs",
      "date": "2026-08-19",
      "status": "ACTIVE",
      "scores": {"novelty": 8, "feasibility": 7, "market_fit": 6},
      "description": "Research phase complete. Verdict: PROCEED (avg 7.0). PRD pending approval.",
      "verdict": "PROCEED",
      "github_url": null,
      "deployed_url": null,
      "linkedin_url": "https://linkedin.com/...",
      "tags": ["cli", "git", "devtools"],
      "categories": ["backend", "tool"],
      "created_at": 1724085000.0,
      "updated_at": 1724086461.0
    }
  ],
  "total": 12,
  "page": 1,
  "total_pages": 2
}
```

**Portfolio alignment:**

| Portfolio field | VentureBot idea field | Notes |
|----------------|----------------------|-------|
| `id` | `id` | idea_tree primary key |
| `title` | `title` | from idea_tree |
| `date` | `created_at` | formatted as YYYY-MM-DD |
| `description` | auto-generated summary | "Research complete. Verdict: X. PRD ready." |
| `fullDescription` | `prd_text` | expands in detail view |
| `githubUrl` | user-provided or Phase 2 repo | null until Phase 2 builds it |
| `deployedUrl` | Phase 2 live demo URL | null until Phase 2 deploys it |
| `linkedinUrl` | user input on `/api/steering` | `LinkedIn post: <url>` → extracted |
| `tags` | extracted from idea text + verdict | e.g., `["cli", "git", "devtools"]` |
| `categories` | Phase 1 ↔ Phase 2 stage | [`"backend"`, `"ai"`, `"fullstack"`] |

### GET /api/ideas query params

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `page` | int | 1 | Page number (10 per page) |
| `category` | string | null | Filter by tag (e.g. "backend") |
| `date_year` | int | null | Filter by year |
| `date_month` | int | null | Filter by month (1-12) |
| `search` | string | null | Search title + description |
| `status` | string | null | Filter by status: ACTIVE, PARK, PRUNED |

### Dynamic tags from idea text

Tags are extracted at checkpoint time (not user-entered). A lightweight
keyword-based approach:

```python
TAG_RULES = {
    "frontend": ["ui", "dashboard", "frontend", "react", "vue", "spa", "web app"],
    "backend": ["api", "backend", "database", "rest", "graphql", "microservice"],
    "fullstack": ["fullstack", "full-stack"],
    "ai": ["ai", "ml", "llm", "gpt", "gemini", "claude", "model", "training"],
    "cli": ["cli", "command line", "terminal", "tui"],
    "tool": ["tool", "utility", "generator", "converter"],
    "mobile": ["ios", "android", "mobile", "app"],
    "devtools": ["git", "debug", "devtools", "ide", "plugin"],
    "infra": ["deploy", "docker", "kubernetes", "ci/cd", "infra"],
    "data": ["data", "etl", "pipeline", "analytics", "visualization"],
}
```

Extracted from the **idea title + research brief + debate transcript**.

## Frontend Design — IdeaTimeline (modeled on portfolio)

The existing `templates/index.html` (376 lines) gets a new "Past Ideas"
panel that mirrors the portfolio's `page.tsx` structure:

### Layout

```
┌────────────────────────────────┬──────────────────────────────────────┐
│  Sidebar (left, 260px)         │  Main Content Area                    │
│  ┌──────────────┐              │  ┌──────────────────────────────────┐ │
│  │  MY IDEAS    │              │  │ Search…                          │ │
│  │  ─────────── │              │  └──────────────────────────────────┘ │
│  │ Categories:  │              │                                       │
│  │ □ ai (6)     │              │  IDEA TIMELINE                        │
│  │ □ backend (4)│              │  ┌─● ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┐ │
│  │ □ tool (7)   │              │  │ CLI tool for git diffs           │ │
│  │ □ frontend(5)│              │  │ Aug 19 · PROCEED (8/7/6)        │ │
│  │ □ fullstack(3)│             │  │ [Short description auto-gen]     │ │
│  │             │              │  │ [Github] [LinkedIn] [Read more]  │ │
│  │ Dates:       │              │  └────────────────────────────────┘ │
│  │ ▼ 2026       │              │  ┌─● ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┐ │
│  │   ▼ Aug (3)  │              │  │ Social media sentiment analyzer │ │
│  │   ▶ Jul (4)  │              │  │ Jul 15 · PARK · awaiting human  │ │
│  │   ▶ Jun (1)  │              │  │ [View PRD] [Resume] [Archive]   │ │
│  │             │              │  └────────────────────────────────┘ │
│  │ Status:      │              │                                       │
│  │ ○ ACTIVE     │              │  Page 1 of 3  [Prev] [Next]          │
│  │ ○ PARK       │              │                                       │
│  │ ○ PRUNED     │              │                                       │
│  └──────────────┘              │                                       │
└────────────────────────────────┴──────────────────────────────────────┘
```

**Reused portfolio patterns (conceptually — not CSS literally):**

1. **Left sidebar** — categories (tags) with counts, date tree with year/month
2. **Search bar** — full width at top, instant filter
3. **Timeline with dots** — vertical line + dots (the "●" markers)
4. **Card layout** — title + date + tags + short description on collapsed card
5. **Action links** — Source (GitHub) / Live Demo (Phase 2) / LinkedIn Post
6. **Expand/collapse** — "Read more" toggles to reveal full PRD
7. **Pagination** — `[Previous] [Page X of Y] [Next]` bar

### Card specificity for IdeaTimeline

The idea card differs from the portfolio card in one dimension: it has
a **status badge** and a **resume action** button — things the portfolio
card doesn't need.

```
┌─────────────────────────────────────────────────────────┐
│ ● CLI tool for git diffs                           🟢 ACTIVE │
│   Aug 19, 2026                                               │
│                                                              │
│   Research complete. Verdict: PROCEED (novelty 8, feasibility│
│   7, market_fit 6). PRD pending human approval.              │
│                                                              │
│   [📝 gh:venturebot-run-42]  [🌐 live demo]  [🔗 LinkedIn]  │
│                                                   [Read more]│
│   ─ ─ ─ ─ ─ ─ ─ ─ ─ expanded state ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │
│   │ Full PRD text…                                           │
│   │ Debate transcript…                                       │
│   │ Scores breakdown…                                        │
│   │ [Continue this idea]  [Archive]                          │
└─────────────────────────────────────────────────────────────┘
```

## Data Architecture

### state.json evolution (additive)

```diff
{
  "run_id": "a925b0b83b03",
  "status": "stopped",
+ "current_idea_id": "abc123...",
  "iteration": 0,
  "messages": [...],
  "tasks": [...],
  "workspace": {"files": []},
  "budget": null
}
```

### idea_tree column usage (existing columns → pipeline population)

| Column | Populated by | When |
|--------|-------------|------|
| `id` | `auto_capture.create_idea()` | Start of Phase 1 run |
| `title` | `idea` parameter | Start of run |
| `status` | `pipeline._save_session()` | Verdict gate / PRD gate |
| `scores` | `pipeline._write_prd()` | After verdict parsed |
| `research_brief` | checkpoint → `update_idea_content()` | After Researcher completes |
| `debate_transcript` | `json.dumps(result.events)` | After debate completes |
| `prd_text` | `result.prd` | After PRD Writer completes |
| `workspace_path` | `f"runs/{run_id}/"` | Start of run |
| `human_intervention_count` | incremented per `/api/steering` call | Each HITL interaction |
| `created_at` / `updated_at` | timestamp | auto |

### New data/ directory layout

```
data/
├── budget.json                    # existing
├── gemini_usage.json              # existing
├── venturebot.db                  # existing (SQLite)
├── checkpoints/                   # NEW — ephemeral, snapshot per phase
│   ├── a925b0b83b03.json          # in-progress debate
│   └── b1c2d3e4f5g6.json          # another in-progress
├── archives/                      # NEW — finished/stopped runs
│   ├── a925b0b83b03.json          # moved from checkpoints on completion
│   └── zzz-old-run.json           # older archives
└── paused_sessions.json           # existing (metadata only, kept for compat)
```

## Implementation Tasks

> **Status (2026-08-19):** Backend (C1–C8) and API (A1–A6) DONE. Tests
> (T1–T3) DONE — 26 tests. Frontend core panel (Past Ideas + in-progress
> checkpoints) DONE; full sidebar/date-tree/CSV polish (F1–F9) still open.

### Milestone I: Persistence Backend (~4h)

| # | Task | File(s) | Est. | Notes |
|---|------|---------|------|-------|
| C1 | Add `checkpoint_dir` to `config.py` | `config.py` | 0.1h | ✅ DONE |
| C2 | Add `save_checkpoint(result, phase)` function | `pipeline.py` | 0.5h | ✅ DONE |
| C3 | Wire checkpoint to every agent turn boundary | `pipeline.py` | 0.5h | ✅ DONE |
| C4 | Add `load_checkpoint(run_id)` function | `pipeline.py` | 0.5h | ✅ DONE |
| C5 | Add `resume_from_checkpoint(run_id)` to pipeline | `pipeline.py` | 1h | ✅ DONE (`_run_pipeline` shared) |
| C6 | Add `finalize_checkpoint(run_id)` — move to archives | `pipeline.py` | 0.25h | ✅ DONE |
| C7 | Add `update_idea_content()` to MemoryStore | `sqlite_store.py` | 0.5h | ✅ DONE |
| C8 | Wire idea_tree population from checkpoint | `pipeline.py` | 0.5h | ✅ DONE (`_archive_result`) |

### Milestone II: API Endpoints (~2h)

| # | Task | Est. | Notes |
|---|------|------|-------|
| A1 | `GET /api/ideas` — paginated list with filters | 1h | ✅ DONE |
| A2 | `GET /api/ideas/{id}` — full detail | 0.25h | ✅ DONE |
| A3 | `POST /api/ideas/{id}/resume` | 0.25h | ✅ DONE (queues title into steering inbox) |
| A4 | `POST /api/ideas/{id}/archive` | 0.15h | ✅ DONE (→ PARK) |
| A5 | `GET /api/checkpoints` | 0.15h | ✅ DONE |
| A6 | `POST /api/checkpoints/{id}/resume` | 0.2h | ✅ DONE |

### Milestone III: Frontend (~5h)

| # | Task | Est. | Notes |
|---|------|------|-------|
| F1 | Reorganize `templates/index.html` into 2-panel layout | 1h | ⏳ partial (inline panel, not sidebar) |
| F2 | Sidebar: categories (tags) with counts | 0.75h | ⏳ tags shown per card, no counts |
| F3 | Sidebar: date tree (year → month) | 0.75h | ⏳ open |
| F4 | Sidebar: status filter radio buttons | 0.25h | ✅ DONE (dropdown) |
| F5 | Search bar component | 0.25h | ✅ DONE |
| F6 | IdeaCard component (collapsed) | 1h | ✅ DONE |
| F7 | IdeaCard expanded state (PRD viewer) | 1h | ✅ DONE (View PRD) |
| F8 | Pagination controls | 0.25h | ✅ DONE |
| F9 | CSV export link | 0.25h | ⏳ open |

### Milestone IV: Tests (~1.5h)

| # | Test file | # tests | What it proves |
|---|-----------|---------|---------------|
| T1 | `tests/test_checkpoint.py` | 9 | ✅ DONE — atomic, loadable, phase rank, archive move |
| T2 | `tests/test_ideas_api.py` | 10 | ✅ DONE — filters, detail, archive, auth gate |
| T3 | `tests/test_ideas_store.py` | 10 | ✅ DONE — update_idea_content, partial, idempotency, migration, tags |

## Summary

| Metric | v1 (draft) | v2 (this document) |
|--------|-----------|-------------------|
| Crash recovery | ❌ None (in-memory only) | ✅ Per-agent checkpoints, atomic writes |
| Resume after restart | ❌ Sessions lost | ✅ `data/checkpoints/<run_id>.json` survives restart |
| Idea archive | Partial (idea_tree never populated) | ✅ Full: PRD, transcript, scores, workspace path |
| UI patterns | Sketchy | ✅ Concrete — mirrors portfolio's Sidebar, ProjectCard, FilterBar, pagination |
| Portfolio alignment | Not addressed | ✅ Same Project shape, same tag/category system, same link slots |
| Backend delta | ~80 lines | ~200 lines (pipeline checkpointing + API) |
| Frontend delta | ~150 lines | ~300 lines (sidebar, cards, filters, pagination) |
| Total estimate | ~6.5h | ~12.5h (persistence 4h + API 2h + UI 5h + tests 1.5h) |