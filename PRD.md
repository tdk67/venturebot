# VentureBot — Product Requirements Document (v2)

**Document ID:** VB-PRD-2026-08-18
**Status:** Final Specification — Ready for Implementation
**Target Platform:** Google ADK (Python) + OpenRouter Fallback
**Target Deploy:** Google Cloud (Vertex AI Agent Engine + Cloud Run)
**References:** idea-01.md, idea-02.md, adk-samples (50+ agents analyzed)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Product Architecture](#2-product-architecture)
3. [Agent Specifications](#3-agent-specifications)
4. [Human-in-the-Loop Touchpoints](#4-human-in-the-loop-touchpoints)
5. [Self-Improvement Layer](#5-self-improvement-layer)
6. [Observability UI](#6-observability-ui)
7. [Coding Agent Migration Path](#7-coding-agent-migration-path)
8. [Memory & State Architecture](#8-memory--state-architecture)
9. [Evaluation Suite](#9-evaluation-suite)
10. [Deployment Architecture](#10-deployment-architecture)
11. [Build Plan & Milestones](#11-build-plan--milestones)
12. [ADK Sample Resources (Reuse Map)](#12-adk-sample-resources-reuse-map)
13. [Non-Functional Requirements](#13-non-functional-requirements)

---

## 1. Executive Summary

### 1.1 What VentureBot Is

VentureBot is a **self-improving, multi-agent research and development system**
built on Google ADK. It takes a vague idea, researches it, subjects it to a
multi-agent debate (Advocate vs. Critic vs. Judge), produces a detailed PRD,
asks the human for approval, and then autonomously implements a working MVP
through a blind TDD loop.

### 1.2 The Complete Pipeline

```
┌────────────────────────────────────────────────────────────────┐
│  PHASE 1: RESEARCH & DEBATE → PRD (Google ADK, Gemini models)  │
│                                                                 │
│  Vague Idea                                                      │
│    ↓                                                            │
│  Research Agent (google_search, web) → Research Brief           │
│    ↓                                                            │
│  [Human Clarification if needed]                                │
│    ↓                                                            │
│  Advocate → Critic → Judge (SequentialAgent)                    │
│         (web search for prior art)                              │
│    ↓                                                            │
│  [Human: Proceed/Abort]                                         │
│    ↓                                                            │
│  PRD Writer → Structured PRD                                    │
│    ↓                                                            │
│  [Human: Approve/Changes/Reject]                                │
└────────────────────────────────┬───────────────────────────────┘
                                 │ approved PRD
                                 ▼
┌────────────────────────────────────────────────────────────────┐
│  PHASE 2: BLIND TDD → MVP (OpenRouter, later ADK)              │
│                                                                 │
│  PRD → PO → TestWriter → pytest → Coder → QA_PO                │
│                                              │                  │
│                                    APPROVE / REVISE (loop ≤ 5)  │
│                                              ↓                  │
│                                         Working MVP             │
└────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌────────────────────────────────────────────────────────────────┐
│  SELF-IMPROVEMENT LAYER (cross-cut, continuous)                │
│                                                                 │
│  Per-turn: auto_capture (learn from each interaction)          │
│  Per-turn: review_fork (what went well/wrong)                  │
│  Nightly: dream_review (consolidate lessons, prune dead ideas) │
│  Pre-compaction: flush_fork (save facts before summarization)  │
└────────────────────────────────────────────────────────────────┘
```

### 1.3 Key Differentiators

1. **Blind debate** — Advocate and Critic have different models and different
   information access (Critic has web search, Advocate doesn't), eliminating
   single-model confirmation bias
2. **Self-improving** — the agent genuinely gets better cycle over cycle through
   background forks and nightly consolidation, not static prompts
3. **Proven coding agent with migration safety** — Phase 2 uses a battle-tested
   OpenRouter pipeline with an anti-degradation gate for ADK migration
4. **Live observability** — every agent thought, every debate turn, every test
   pass/fail is streamed to a live dashboard

---

## 2. Product Architecture

### 2.1 System Diagram

```
                       ┌──────────────────────────────────────┐
                       │  HUMAN (Observability UI :8080)       │
                       │  Chat · Idea Tree · Kanban · Metrics │
                       └──────┬──────────┬──────────┬─────────┘
                              │ SSE      │ POST     │
                       ┌──────▼──────────▼──────────▼─────────┐
                       │  FASTAPI APPLICATION (unified)        │
                       │  Serves: UI + Phase 1 API + Phase 2  │
                       │  API + Dream Review endpoint         │
                       └──┬──────────┬──────────┬─────────────┘
                          │          │          │
            ┌─────────────▼──┐ ┌─────▼──────┐ ┌─▼──────────────┐
            │ PHASE 1        │ │ PHASE 2    │ │ SELF-IMPROVE   │
            │ ADK AGENTS     │ │ BLIND TDD  │ │ LAYER          │
            │                │ │            │ │                │
            │ Researcher     │ │ PO         │ │ auto_capture   │
            │ Advocate       │ │ TestWriter │ │ review_fork    │
            │ Critic         │ │ Coder      │ │ dream_review   │
            │ Judge          │ │ QA_PO      │ │ idea_tree      │
            │ PRD Writer     │ │ Gate Ctrl  │ │ flush_fork     │
            │                │ │ (shadow)   │ │                │
            └───────┬────────┘ └──────┬──────┘ └───────┬────────┘
                    │                │                  │
                    ▼                ▼                  ▼
            ┌──────────────────────────────────────────────────┐
            │            SHARED STATE / MEMORY                 │
            │  Stage 1 (VPS): SQLite + JSON file               │
            │  Stage 3 (GCP): Memory Bank (Vertex AI)          │
            │  Idea Tree: SQLite table with pruning            │
            │  Agent Memory: lessons, techniques, profile      │
            └──────────────────────────────────────────────────┘
```

### 2.2 Technology Stack

| Layer | Stage 1 (VPS) | Stage 3 (GCP Target) |
|-------|--------------|---------------------|
| Agent Framework | Google ADK (Python) | Google ADK (Python) |
| Phase 1 Models | Gemini 3.7 Flash / 3.1 Pro (configurable) | Gemini 3.7 Flash / 3.1 Pro (configurable) |
| Phase 2 Models | OpenRouter (deepseek-v4-pro, deepseek-chat-v3) | ADK (Gemini) + OpenRouter fallback |
| Memory | SQLite + JSON | Memory Bank (Vertex AI) |
| Sessions | ADK InMemorySessionService | Agent Platform Sessions |
| Web Research | `google_search` (built-in ADK tool) | Same |
| UI | FastAPI + HTML + SSE | Same (or Vite based on Long Horizon) |
| Deployment | Local venv + uvicorn | Agent Engine + Cloud Run |
| Auth | None (local) | IAP (Identity-Aware Proxy) |
| Secrets | .env file | Secret Manager |

---

## 3. Agent Specifications

### 3.1 Research Agent

**Model:** `gemini-3.7-flash` (configurable via `VENTUREBOT_MODEL_RESEARCHER`)
**ADK Pattern:** `LlmAgent` with tools
**Reference:** `academic-research/academic_research/agent.py`, `market-research-agent/app/agent.py`
**Tools:** `google_search` (built-in), `clarify_question` (custom HITL tool)

**System Prompt:**

```
You are a Research Analyst. Your job is to investigate a vague idea
and produce a structured research briefing.

Workflow:
1. Parse the user's idea. If it is too vague, call `clarify_question`
   to ask ONE specific question. Wait for the answer, then continue.
2. Call `google_search` to find:
   - GitHub repositories: similar open-source projects, stars, activity
   - Prior art: existing products/services in this space
   - Market signals: forum discussions, trends, funding
   - Technical feasibility: APIs, SDKs, libraries needed
3. Synthesize into a Research Brief with these sections:
   - Idea Summary (2-3 sentences)
   - Prior Art (existing products/projects with URLs, gaps identified)
   - Market Signals (demand evidence, audience size)
   - Technical Landscape (required stack, libraries, APIs)
   - Resource Links (URLs to key repos, docs, papers)
   - Open Questions (what's still unknown)

Output the research brief as structured markdown. Include URLs for
every finding so the Advocate and Critic can verify.
```

**Structured Output Schema:**

```json
{
  "idea_summary": "string",
  "prior_art": [{"name": "string", "url": "string", "gap": "string"}],
  "market_signals": [{"source": "string", "url": "string", "insight": "string"}],
  "technical_landscape": {"required_apis": ["string"], "libraries": ["string"], "platforms": ["string"]},
  "resource_links": ["string (URL)"],
  "open_questions": ["string"],
  "needs_clarification": false,
  "clarification_question": null
}
```

### 3.2 Advocate Agent

**Model:** `gemini-3.7-flash` (configurable via `VENTUREBOT_MODEL_ADVOCATE`)
**ADK Pattern:** `Agent` in `SequentialAgent` chain
**Reference:** `llm-auditor/llm_auditor/sub_agents/critic/agent.py` (tool pattern)
**Input:** Research Brief from Research Agent
**Tools:** None (Advocate argues from the brief, not from web search — blind separation)

**System Prompt:**

```
You are the Advocate. Your job is to argue passionately and rigorously
FOR the idea. You must build the strongest possible case.

Given the Research Brief, argue for:

1. UNIQUENESS — Why this idea fills a genuine gap. What makes it
   different from every prior art entry in the brief. If the brief
   lists competitors, explain why they don't solve the full problem.

2. MARKET NEED — Who needs this? What pain does it solve? Why will
   they pay attention or money? Use evidence from the brief's
   market signals.

3. TECHNICAL FEASIBILITY — The brief's technical landscape shows the
   pieces exist. How would you assemble them into an MVP? Propose a
   concrete architecture: tech stack, data flow, key components.

4. ARCHITECTURE PROPOSAL — Propose a specific architecture for the
   MVP. What runs where? How do components communicate? What's the
   minimal viable scope (buildable in ~10 hours)?

5. WHY NOW — What makes this moment right? Is there a trend, a
   platform shift, a new capability that makes this possible today?

Structure your argument clearly with sections. Be specific, not
hand-wavy. Cite the brief's findings by name.
```

### 3.3 Critic Agent

**Model:** `gemini-3.1-pro` (configurable via `VENTUREBOT_MODEL_CRITIC`)
**ADK Pattern:** `Agent` with `google_search` tool
**Reference:** `llm-auditor/llm_auditor/sub_agents/critic/agent.py` (exact pattern to follow)
**Input:** Research Brief + Advocate's argument
**Tools:** `google_search` (built-in) — Critic CAN search the web to fact-check and find counter-evidence

**System Prompt:**

```
You are the Red-Team Critic. Your job is to analyze EVERY claim made
by the Advocate and verify or challenge it with evidence. You have
access to google_search to find counter-evidence.

For each section of the Advocate's argument:

1. UNIQUENESS CHALLENGE — Search for products/projects the Advocate
   missed. If you find a direct competitor the brief didn't list,
   cite it with URL and explain why it invalidates the uniqueness claim.

2. MARKET REALITY CHECK — Search for evidence that the market need
   is smaller than claimed, or that previous attempts failed. Look
   for: abandoned GitHub projects, failed startups, low search volume.

3. TECHNICAL SKEPTICISM — Challenge every technical assumption:
   - Is the proposed architecture over-engineered? Propose simpler.
   - Are there hidden costs (API pricing, scaling, maintenance)?
   - What happens when edge case X occurs?
   - Is there a license issue with any proposed library?

4. ARCHITECTURE CRITIQUE — Identify specific weaknesses:
   - Single points of failure
   - Vendor lock-in risks
   - Performance bottlenecks under realistic load
   - Security gaps in the proposed design

5. TIMING CHALLENGE — Is this actually the right moment, or is the
   Advocate manufacturing urgency?

Each challenge MUST cite a source: either the research brief (by name),
the Advocate's own words (quote them), or a google_search result (with URL).

At the end, summarize: what are the 3-5 most critical risks?
```

### 3.4 Judge Agent

**Model:** `gemini-3.1-pro` (configurable via `VENTUREBOT_MODEL_JUDGE`)
**ADK Pattern:** `Agent` in `SequentialAgent` chain (no tools needed)
**Input:** Research Brief + Advocate argument + Critic rebuttal
**Output:** Structured verdict JSON

**System Prompt:**

```
You are the Feasibility Judge. You have read:
- The original Research Brief
- The Advocate's case FOR the idea
- The Critic's challenges and counter-evidence

Your job is to weigh both sides and produce a structured verdict.
Be fair, evidence-based, and decisive. Do not hedge.

Scoring Rubric:

NOVELTY (1-10):
  1-3: Multiple direct competitors, no differentiation
  4-6: Some differentiation in a crowded space
  7-8: Few direct competitors, clear gap
  9-10: Completely novel, no comparable solution exists

FEASIBILITY (1-10):
  1-3: Requires technology that doesn't exist or 100+ hours
  4-6: Possible but with significant technical unknowns
  7-8: Standard technology, well-understood, ~10 hour build
  9-10: Trivial to build, all pieces exist as off-the-shelf

MARKET FIT (1-10):
  1-3: No evidence of demand, solving non-problem
  4-6: Unclear demand, niche audience
  7-8: Clear demand signals, defined audience
  9-10: Proven demand, large addressable market, timing is perfect

OVERALL VERDICT:
  ≥7 average: PROCEED — idea is worth building
  4-6 average: PARK — promising but needs more research
  <4 average: PRUNE — not worth pursuing now

Also produce an ARCHITECTURE DECISION RECORD documenting the key
architecture decisions that survived the debate, with rationale.
```

**Structured Output Schema:**

```json
{
  "scores": {
    "novelty": {"score": 0, "rationale": "string"},
    "feasibility": {"score": 0, "rationale": "string"},
    "market_fit": {"score": 0, "rationale": "string"},
    "overall_average": 0
  },
  "verdict": "PROCEED | PARK | PRUNE",
  "verdict_rationale": "string",
  "key_risks": ["string"],
  "architecture_decisions": [
    {
      "topic": "string",
      "decision": "string",
      "advocate_position": "string",
      "critic_position": "string",
      "chosen_approach": "string",
      "rationale": "string"
    }
  ]
}
```

### 3.5 PRD Writer Agent

**Model:** `gemini-3.1-pro` (configurable via `VENTUREBOT_MODEL_PRD_WRITER`)
**ADK Pattern:** `LlmAgent` (no tools)
**Reference:** `sdlc-task-planner/sdlc_task_planner/prompt.py` (structured output format)
**Input:** Research Brief + Debate Transcript + Judge's Verdict + Architecture Decisions

**System Prompt:**

```
You are a Technical Product Manager. Given the research, debate,
and architecture decisions, write a detailed, implementable PRD.

The PRD must contain:

1. PRODUCT OVERVIEW
   - What is being built (one paragraph)
   - Target user persona
   - Core value proposition

2. FUNCTIONAL REQUIREMENTS
   - Numbered FR-1, FR-2, etc.
   - Each must be testable: a human or LLM must be able to write
     a test that verifies it

3. NON-FUNCTIONAL REQUIREMENTS
   - Performance, security, usability constraints

4. TECHNICAL ARCHITECTURE
   - Based on the Architecture Decision Records from the debate
   - Tech stack with versions
   - Data flow diagram (describe in text, suitable for mermaid)
   - Component breakdown

5. ACCEPTANCE CRITERIA
   - AC-1, AC-2, etc. in Given/When/Then format
   - Each must correspond to a functional requirement

6. MILESTONES & MVP SCOPE
   - What's in the MVP (Phase 2 will build this)
   - What's explicitly out of scope
   - Estimated effort

7. OPEN QUESTIONS & RISKS
   - Any unresolved issues from the debate
   - Mitigation strategies for the top risks

Output the complete PRD as structured markdown. Use the
sdlc-user-story-refiner's format as a reference for quality.
```

### 3.6 Phase 2 Agents (PO, TestWriter, Coder, QA_PO)

**These are already built and working** in the custom Python implementation
(`blind_tdd/agents.py`). They use OpenRouter models:

| Agent | Model | Responsibility |
|-------|-------|----------------|
| PO | `deepseek/deepseek-v4-pro` | Parse PRD → structured acceptance criteria (JSON) |
| TestWriter | `deepseek/deepseek-chat-v3-0324` | Write pytest tests from PRD only (blind) |
| Coder | `deepseek/deepseek-chat-v3-0324` | Write implementation from test failures only (blind) |
| QA_PO | `deepseek/deepseek-v4-pro` | Review implementation against PRD → APPROVE/REVISE |

**When migrating to ADK (Stage 3):** These agents become ADK `Agent` instances with
`response_schema` enforcement. The TestWriter and Coder get `sandbox.terminal`
tools. The Coder Shadow Mode metrics collector gates the transition.

---

## 4. Human-in-the-Loop Touchpoints

### 4.1 Clarification Gate (Research Agent)

**Trigger:** Research Agent detects contradictory information, missing domain
expertise, or an underspecified idea.

**Mechanism:** `clarify_question(question)` tool call → UI shows a question card →
human types answer → answer is injected as function response → agent resumes.

**ADK Implementation:** Follows the same pattern as Pi's clarify. In ADK, this
uses `long_running_operation` / `input_required` status. The UI detects
`input-required` tasks and renders the question with a text input.

**Reference:** Long Horizon's `ask_parent` escalation in `subagents/delegate_runner.py`,
plus the `clarify` tool pattern in `horizon/tools/`.

### 4.2 Verdict Gate (After Debate)

**Trigger:** Judge produces verdict with scores.

**Behavior:**
- If all scores ≥ 6: auto-proceed to PRD Writer
- If any score < 6: UI shows verdict + scores + [PROCEED ANYWAY] [ABORT] buttons
- Human clicks button → action is routed back to the agent

### 4.3 PRD Approval Gate

**Trigger:** PRD Writer produces PRD.

**Behavior:**
- PRD is rendered in the UI with [APPROVE] [REQUEST CHANGES] [REJECT] buttons
- [APPROVE] → bridge triggers Phase 2 with the approved PRD
- [REQUEST CHANGES] → UI prompts for feedback text → feedback prepended to
  Research Agent context → loop restarts
- [REJECT] → idea archived as PRUNED, session ends

### 4.4 Chat Interface

The UI chat panel allows the human to send free-text messages to the agent at
any time during Phase 1. The agent processes these as additional context.

---

## 5. Self-Improvement Layer

### 5.1 Architecture (Three-Fork Pattern)

```
                   ┌───────────────────────────────┐
                   │  AGENT TURN COMPLETES          │
                   │  (user gets response first)    │
                   └───────────────┬───────────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              │
           ┌──────────────┐ ┌──────────────┐      │
           │ FORK 1       │ │ FORK 2       │      │
           │ auto_capture │ │ review_fork  │      │
           │ (direct)     │ │ (LLM call)   │      │
           │              │ │              │      │
           │ Save session │ │ Analyze:     │      │
           │ events to    │ │ - What went  │      │
           │ memory store │ │   well?      │      │
           │ for later    │ │ - What went  │      │
           │ retrieval    │ │   wrong?     │      │
           │              │ │ - New ideas? │      │
           │              │ │ - Prune old? │      │
           └──────┬───────┘ └──────┬───────┘      │
                  │                │              │
                  ▼                ▼              │
           ┌──────────────────────────────────┐   │
           │  MEMORY STORE (SQLite / MemBank) │   │
           │  - session facts                 │   │
           │  - agent lessons                 │   │
           │  - technique library             │   │
           │  - user profile                  │   │
           └──────────────────────────────────┘   │
                                                  │
           ┌──────────────────────────────────────┘
           │  NIGHTLY (cron / scheduler)
           ▼
   ┌──────────────────────────────────────────────┐
   │ FORK 3: dream_review                         │
   │                                              │
   │ 1. Load ALL recent sessions (last 24h)       │
   │ 2. Extract: all lessons, judgments, facts    │
   │ 3. Consolidate (LLM-assisted):               │
   │    - Deduplicate similar lessons             │
   │    - Resolve contradictory lessons           │
   │    - Update user profile                     │
   │    - Prune dead-end ideas from idea tree     │
   │    - Promote new techniques to library        │
   │    - Retire techniques that caused failures  │
   │ 4. Write consolidated state back             │
   └──────────────────────────────────────────────┘
```

### 5.2 Fork 1: auto_capture

**Reference:** `core/python/cross-session-memory/app/agent.py` (`after_agent_callback`)
and `core/python/long-horizon-harness/horizon/memory/auto_capture.py`

**Implementation:**
```python
# On the Agent definition:
after_agent_callback=generate_memories_callback

async def generate_memories_callback(callback_context: CallbackContext):
    await callback_context.add_session_to_memory()
```

In Stage 1 (VPS), uses a custom SQLite store. In Stage 3 (GCP), uses
`VertexAiMemoryBankService` — same API, different backend.

**Throttling:** 120s cooldown per session (from Long Horizon's `_throttle.py`).

### 5.3 Fork 2: review_fork

**Reference:** `core/python/long-horizon-harness/horizon/memory/review_fork.py`

**Behavior:** Fire-and-forget LLM call that analyzes the just-completed turn:

```
You are VentureBot's self-improvement engine. Analyze this turn.

SESSION EVENTS:
{full_transcript}

CURRENT AGENT MEMORY:
{current_lessons}

Analyze:
1. What did the agent do WELL? (technique to reinforce)
2. What did the agent do POORLY? (mistake to avoid repeating)
3. Would a different approach have produced a better result?
4. Should this idea stay ACTIVE, be PARKED, or PRUNED?
5. What ONE new rule/technique should be added to agent memory?

Output JSON:
{
  "reinforce": ["string technique name"],
  "avoid": ["string mistake description"],
  "new_technique": null or {"name": "string", "rule": "string"},
  "retire_technique": null or "string technique name",
  "idea_status": "ACTIVE | PARK | PRUNE",
  "idea_status_reason": "string"
}
```

**Throttling:** Same 120s cooldown as auto_capture. Runs via
`SiblingAgentPlugin` (Long Horizon pattern) — non-blocking, fire-and-forget.

### 5.4 Fork 3: dream_review (Nightly)

**Reference:** `core/python/long-horizon-harness/horizon/memory/dream_review.py`
(exact pattern to follow) and `horizon/scheduler/dream_review_endpoint.py`

**Endpoint:** `POST /scheduler/dream-review`

**Algorithm:**

1. `list_active_users(session_service, since_ts=now - 24h)` — discover who was active
2. For each active user:
   - `_collect_user_sessions(session_service, limit=50)` — load recent sessions
   - Filter to text-bearing events (skip tool-call noise)
   - Call `memories.generate(app_name, user_id, events, consolidate=True)`
3. The LLM consolidation prompt (retrospective):
   ```
   You are VentureBot's nightly self-improvement engine.

   Review the following sessions from today:

   {session_1_transcript}
   {session_2_transcript}
   ...

   Current state:
   - Profile: {current_profile}
   - Lessons: {current_lessons}
   - Techniques: {current_techniques}
   - Idea Tree: {current_idea_tree}

   TASKS:
   1. CONSOLIDATE LESSONS: Merge similar lessons, resolve contradictions.
      Delete lessons that were one-off mistakes, keep recurring patterns.

   2. UPDATE PROFILE: What did you learn about the user? Preferences,
      style, recurring decisions, technical preferences?

   3. PRUNE IDEA TREE: For each idea in the tree:
      - Score < 5 and no human intervention → PRUNE
      - No activity in 7 days → PARK
      - Rising scores or human attention → keep ACTIVE

   4. CURATE TECHNIQUES: Promote techniques that led to approvals,
      retire techniques associated with repeated failures.

   Output JSON: {consolidated_lessons, profile_updates, idea_tree_changes,
                 promoted_techniques, retired_techniques}
   ```

4. Write consolidated state back to memory store

**Stage 1 (VPS) implementation:** Triggered manually via `/dream-review` slash
command or a simple cron job hitting the endpoint. Uses SQLite instead of
Memory Bank — same algorithm, different storage.

### 5.5 Idea Tree with Pruning

**Inspiration:** Pi's tree-pruning context management, adapted to idea-level pruning.

**Schema (SQLite):**

```sql
CREATE TABLE idea_tree (
    id TEXT PRIMARY KEY,
    parent_id TEXT,                    -- NULL for root ideas
    title TEXT NOT NULL,
    status TEXT DEFAULT 'ACTIVE',      -- ACTIVE, PARK, PRUNED
    scores TEXT,                        -- JSON: {novelty, feasibility, market_fit}
    research_brief TEXT,                -- JSON
    debate_transcript TEXT,
    prd_text TEXT,
    workspace_path TEXT,
    human_intervention_count INTEGER DEFAULT 0,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    last_dream_review TIMESTAMP,
    pruned_at TIMESTAMP,
    pruned_reason TEXT
);
```

**Pruning rules (enforced by dream_review):**
- Score < 5 with 0 human interventions → PRUNE after 24h
- Score < 5 with ≥1 human intervention → PARK (don't prune, human wants it)
- No activity in 7 days → PARK
- PARKED for 30 days → PRUNE
- Human explicitly REJECTED → PRUNE (but keep record with reason)
- Human explicitly APPROVED and Phase 2 succeeded → keep ACTIVE (the MVP exists)

---

## 6. Observability UI

### 6.1 Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  VentureBot                                         [▶Run] [⏹Stop] [↻Reset] │
├──────────┬────────────────────────────────┬──────────────────────┤
│          │                                │                      │
│ 📋 IDEA  │ 🔬 LIVE DEBATE TRANSCRIPT      │ 🧠 SELF-IMPROVE     │
│ TREE     │                                │                      │
│          │ [Research Agent]: Searching    │ Dream Review:        │
│ 🟢 idea1 │  for prior art on GitHub...    │  Last night found:   │
│  8.2     │  Found: 23 repos               │  3 new techniques    │
│          │                                │  2 contradictions    │
│ 🟢 idea2 │ [Advocate]: "This is unique    │    resolved           │
│  9.1     │  because..." (streaming)       │                      │
│          │                                │ 📈 Improvement Trend │
│ 🟡 idea3 │ [Critic]: "But X exists at    │  Pass rate: 85%→92%   │
│  5.4     │  github.com/..."               │  Iterations: 3.2→2.1 │
│          │  (with source link)            │  Ideas pruned: 4     │
│ 🔴 idea4 │                                │                      │
│  2.1     │ [Judge]:                       │ 🛠️ Technique Library │
│          │  Novelty: 8/10 ✅              │  • "Search GitHub   │
│          │  Feasibility: 7/10 ✅          │    before debating"  │
│          │  Market Fit: 6/10 ⚠️            │  • "Ask clarify when│
│          │  Verdict: PROCEED              │    idea < 50 words"  │
│          │                                │  • "Check license    │
│          │  [PROCEED ANYWAY] [ABORT]      │    before suggesting │
│          │                                │    library"          │
│          │ ──────────────────────────────│                      │
│          │ 🏗️ IMPLEMENTATION              │                      │
│          │                                │                      │
│          │ Kanban:  Iter 3/5  [■■■■□]    │                      │
│          │ ✅ PO    ✅ TestWriter          │                      │
│          │ 🔄 Coder ⬜ QA_PO              │                      │
│          │                                │                      │
│          │ Workspace: test_venture.py,    │                      │
│          │            venture.py           │                      │
│          │                                │                      │
│          │ 💬 CHAT                        │                      │
│          │ ┌────────────────────────────┐ │                      │
│          │ │ [Agent]: Here is the PRD.  │ │                      │
│          │ │         [APPROVE][CHANGES] │ │                      │
│          │ │ [Human]: APPROVE           │ │                      │
│          │ │ [Agent]: Phase 2 started.  │ │                      │
│          │ │ ▯ Type your message...  [▶]│ │                      │
│          │ └────────────────────────────┘ │                      │
└──────────┴────────────────────────────────┴──────────────────────┘
```

### 6.2 UI Features

| Feature | Implementation | Refresh |
|---------|---------------|---------|
| Idea Tree (left panel) | Rendered from `idea_tree` SQLite table | Poll every 5s |
| Debate Transcript (center top) | SSE stream of agent messages | Real-time SSE |
| Implementation Kanban (center mid) | From `state.json` via Phase 2 dashboard | Poll every 1s |
| Implementation Progress | Iteration count + pass/fail + bar | Poll every 1s |
| Chat (center bottom) | SSE + POST for human messages | Real-time SSE |
| Self-Improvement Console (right) | Rendered from memory store | Poll every 10s |
| Technique Library | List of techniques with promote/retire status | Poll every 10s |
| Verdict/Approval Buttons | Rendered inline in debate transcript | On-demand |

### 6.3 API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serves the HTML dashboard |
| GET | `/api/state` | Full current pipeline state (JSON) |
| POST | `/api/reset` | Reset Phase 2 state |
| POST | `/api/run-phase1` | Start Phase 1 with `{"idea": "..."}` |
| POST | `/api/clarify-response` | Human's answer to a clarification question `{"answer": "..."}` |
| POST | `/api/verdict-action` | Human's response to verdict `{"action": "proceed" | "abort"}` |
| POST | `/api/prd-action` | Human's response to PRD `{"action": "approve" | "changes" | "reject", "feedback": "..."}` |
| POST | `/api/run-phase2` | Trigger Phase 2 with approved PRD |
| POST | `/api/stop` | Stop active run |
| POST | `/scheduler/dream-review` | Trigger dream review pass |
| GET | `/api/idea-tree` | Current idea tree state |
| GET | `/api/memories` | Agent memories and techniques |
| GET | `/api/metrics` | Self-improvement metrics |

---

## 7. Coding Agent Migration Path (Phase 2 → ADK)

### 7.1 Stage 1: Custom Python + OpenRouter (Current)

Phase 2 runs on the proven custom pipeline. ADK is not involved in code generation.

### 7.2 Stage 2: Shadow Mode

Both the custom pipeline AND an ADK Gemini coding agent run in parallel on
the same PRD. The custom output is delivered to the user; the ADK output is
logged for comparison.

**Metrics collected across N runs (target: 10):**

| Metric | Custom+OR baseline | ADK shadow | Threshold to promote |
|--------|-------------------|------------|---------------------|
| Test pass rate | baseline | shadow | shadow ≥ 95% of baseline |
| Iterations to converge | baseline | shadow | shadow ≤ baseline |
| Avg code quality score* | baseline | shadow | shadow ≥ baseline |
| Avg cost per run | baseline | shadow | shadow ≤ 2× baseline |

*Code quality: LLM-as-judge rubric on correctness, readability, extensibility,
edge-case handling. Both outputs are scored by the same judge (Gemini 3.1 Pro).

### 7.3 Stage 3: ADK Primary + Fallback

- ADK takes over as primary coding agent
- Custom+OR stays as **permanently hot fallback**
- Anti-degradation monitor runs continuously:
  - **If ADK pass rate drops below 80%:** auto-revert to fallback, alert human
  - **If ADK cost exceeds 2× fallback:** alert human, don't auto-revert

### 7.4 Stage 4: Full ADK (Optional, Post-Hackathon)

If ADK consistently outperforms custom+OR across 50+ runs, the fallback can be
retired. But the anti-degradation monitor stays forever.

### 7.5 `coder_shadow` ADK Agent Specification

```python
# research_debate/sub_agents/coder_shadow/agent.py

from google.adk.agents import Agent
from google.adk.tools import google_search  # for debugging

coder_shadow = Agent(
    model="gemini-3.1-pro",  # configurable via VENTUREBOT_MODEL_CRITIC_SHADOW
    name="coder_shadow",
    description=(
        "ADK-native coding agent that implements Python code from test "
        "failure output. Runs in shadow mode during Stage 2 migration."
    ),
    instruction=CODER_SHADOW_PROMPT,
    tools=[
        # In Stage 3, these would include sandbox.terminal for running tests
    ],
)
```

**Key Pi patterns translated to ADK:**

| Pi Pattern | ADK Equivalent |
|-----------|---------------|
| Tool-giving (read/write/bash) | `sandbox.terminal` + `sandbox.file_ops` |
| Context tree-pruning | `EventsCompactionConfig` + custom summarizer |
| Multi-turn reasoning | ADK `Runner` with resumable sessions |
| Structured output enforcement | ADK `response_schema` on `LlmAgent` |
| Error recovery (retry) | `HttpRetryOptions(attempts=3)` on `Gemini(...)` |
| bash tool output parsing | `after_tool_callback` to parse and structure tool output |
| Human clarification | `long_running_operation` → input-required status |

---

## 8. Memory & State Architecture

### 8.1 Memory Stores

| Store | Stage 1 (VPS) | Stage 3 (GCP) | Content |
|-------|--------------|---------------|---------|
| Session facts | SQLite `session_facts` table | Memory Bank | What happened in each turn |
| Agent lessons | SQLite `agent_lessons` table | Memory Bank + Custom topics | Techniques, mistakes, rules |
| User profile | JSON file + SQLite | Memory Bank Structured Profile | User preferences, style, recurring decisions |
| Idea tree | SQLite `idea_tree` table | SQLite or Firestore | Idea nodes with scores, status |
| Phase 2 state | JSON file (`state.json`) | JSON file or Cloud SQL | Current run state |

### 8.2 Memory Bank Configuration (Stage 3)

**Reference:** `core/python/cross-session-memory/app/app_utils/memory_config.py`
(exact code to adapt)

```python
from vertexai._genai.types import (
    ManagedTopicEnum,
    MemoryBankCustomizationConfig as CustomizationConfig,
    MemoryBankCustomizationConfigMemoryTopic as MemoryTopic,
    MemoryBankCustomizationConfigMemoryTopicManagedMemoryTopic as ManagedMemoryTopic,
    MemoryBankCustomizationConfigMemoryTopicCustomMemoryTopic as CustomMemoryTopic,
    ReasoningEngineContextSpecMemoryBankConfig as MemoryBankConfig,
)

memory_bank_config = MemoryBankConfig(
    customization_configs=[
        CustomizationConfig(
            memory_topics=[
                # Standard topics (from cross-session-memory sample)
                MemoryTopic(
                    managed_memory_topic=ManagedMemoryTopic(
                        managed_topic_enum=ManagedTopicEnum.USER_PERSONAL_INFO,
                    ),
                ),
                MemoryTopic(
                    managed_memory_topic=ManagedMemoryTopic(
                        managed_topic_enum=ManagedTopicEnum.USER_PREFERENCES,
                    ),
                ),
                MemoryTopic(
                    managed_memory_topic=ManagedMemoryTopic(
                        managed_topic_enum=ManagedTopicEnum.EXPLICIT_INSTRUCTIONS,
                    ),
                ),
                # Custom VentureBot topics
                MemoryTopic(
                    custom_memory_topic=CustomMemoryTopic(
                        label="agent_techniques",
                        description="Coding and debate techniques the agent has "
                                    "learned. Include the technique name, when to "
                                    "use it, and evidence of its effectiveness.",
                    ),
                ),
                MemoryTopic(
                    custom_memory_topic=CustomMemoryTopic(
                        label="idea_evaluations",
                        description="Past idea evaluations with scores, verdicts, "
                                    "and the key arguments that led to each decision. "
                                    "Track which arguments were most persuasive.",
                    ),
                ),
                MemoryTopic(
                    custom_memory_topic=CustomMemoryTopic(
                        label="architecture_decisions",
                        description="Architecture Decision Records from past debates. "
                                    "Include the topic, the options considered, the "
                                    "chosen approach, and the outcome (did it work?).",
                    ),
                ),
            ],
        ),
    ],
)
```

### 8.3 Memory Wiring (ADK Agent)

**Reference:** `core/python/cross-session-memory/app/agent.py` (exact pattern)

Two lines enable cross-session memory:
```python
root_agent = Agent(
    ...
    tools=[..., PreloadMemoryTool()],          # recall: injects memories at turn start
    after_agent_callback=generate_memories_callback,  # write: saves after each turn
)
```

---

## 9. Evaluation Suite

### 9.1 Phase 1 Eval Cases

**Reference:** `idea-01.md` §5, adapted with structured expected outputs.

| ID | Input | Expected Agent Flow | Pass Criteria |
|----|-------|--------------------|---------------|
| **E-01** | "Build an AI tool that summarizes unread Gmail emails" | Researcher finds 50+ products. Critic flags zero differentiation. Judge scores Novelty ≤ 3. | Outcome: PRUNE. Novelty ≤ 3. At least 3 prior art entries cited with URLs. |
| **E-02** | "Build a local LLM on Raspberry Pi that predicts stock market with 99% accuracy" | Critic flags hardware impossibility. Advocate fails to produce viable defense. Judge scores Feasibility ≤ 2. | Outcome: PRUNE. Feasibility ≤ 2. Technical rationale recorded. |
| **E-03** | "Long-Horizon Research Buddy with Red-Team Debate using Google ADK & sleeping loops" | Advocate highlights hackathon relevance. Critic challenges scope. Judge verifies open-source components. | Outcome: PROCEED. Novelty ≥ 8, Feasibility ≥ 7, Market Fit ≥ 7. |
| **E-04** | "Something with AI and PDF reporting" | `clarify_question()` fires. After human answer, Advocate proposes 3 angles. Critic eliminates basics. Judge picks best niche. | Outcome: enriched niche proposal returned. Clarification was used. |
| **E-05** | Simulated budget limit breach ($2.00 cap) | Rate limiter interceptor blocks further LLM calls. Agent halts gracefully. | Outcome: Agent halts, UI shows budget alert, state is saved. |

### 9.2 Self-Improvement Eval Cases (New)

| ID | Input | Expected Flow |
|----|-------|---------------|
| **E-06** | Run identical PRD 3 times with agent memory enabled | Iteration 1 baseline. Iteration 3: the agent should have learned from iteration 1's mistakes. Convergence iterations should decrease. |
| **E-07** | Dream review after 10 diverse sessions | Profile correctly consolidates user preferences. Contradictions resolved. Dead ideas pruned. |
| **E-08** | Shadow mode: 10 PRDs through both ADK coder AND custom coder | ADK metrics compared to custom baseline. Gate decision is correct based on the 95% threshold. |

### 9.3 Eval Harness (ADK Pattern)

**Reference:** `python/agents/travel-concierge/eval/test_eval.py`,
`core/python/long-horizon-harness/tests/eval/`

```bash
# Phase 1 eval (ADK eval runner)
agents-cli eval run

# Phase 2 eval (pytest, already working)
./venv/bin/pytest workspace/
```

---

## 10. Deployment Architecture

### 10.1 Stage 1: VPS (Development)

```
/root/venturebot/
├── research_debate/          # ADK application
│   ├── __init__.py           # env loading
│   ├── agent.py              # root_agent (SequentialAgent chain)
│   ├── sub_agents/
│   │   ├── researcher/        # agent.py + prompt.py
│   │   ├── advocate/         # agent.py + prompt.py
│   │   ├── critic/           # agent.py + prompt.py
│   │   ├── judge/            # agent.py + prompt.py
│   │   ├── prd_writer/       # agent.py + prompt.py
│   │   └── coder_shadow/     # agent.py + prompt.py (Stage 2)
│   ├── tools/
│   │   ├── clarify.py        # HITL clarification tool
│   │   └── web_research.py   # Extended research utilities
│   ├── memory/
│   │   ├── sqlite_store.py   # Session facts
│   │   ├── auto_capture.py   # Fork 1
│   │   ├── review_fork.py    # Fork 2
│   │   ├── dream_review.py   # Fork 3
│   │   └── idea_tree.py     # Idea tree CRUD
│   ├── deployment/
│   │   └── deploy.py         # GCP deploy (Stage 3)
│   └── pyproject.toml
├── blind_tdd/                # Phase 2 (already built)
│   ├── config.py
│   ├── llm_client.py
│   ├── agents.py
│   ├── venturebot_harness.py
│   ├── sim_store.py
│   └── dashboard.py
├── bridge.py                 # Phase 1 → Phase 2 handoff
├── unified_dashboard.py      # Combined Phase 1 + 2 + Self-Improvement UI
├── scheduler.py              # Dream review cron (Stage 2+)
└── shared_state.json
```

Run:
```bash
cd /root/venturebot
uv run adk web research_debate            # ADK dev UI
./venv/bin/uvicorn unified_dashboard:app --host 0.0.0.0 --port 8080  # Our custom UI
```

### 10.2 Stage 3: GCP (Hackathon Demo)

**Reference:** `python/agents/travel-concierge/deployment/deploy.py` (exact pattern),
`core/python/cross-session-memory/app/app_utils/deploy.py` (Agent Engine +
Memory Bank pattern)

```
┌───────────────────────────────────────────────────────────┐
│  GCP Project: venturebot-hackathon                        │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ Agent Engine (Vertex AI)                             │ │
│  │ ┌─────────────────────────────────────────────────┐ │ │
│  │ │ AdkApp(root_agent=venturebot_agent)              │ │ │
│  │ │   ↓                                             │ │ │
│  │ │ Agent Platform Sessions (resumable)              │ │ │
│  │ │ Memory Bank (cross-session memory)               │ │ │
│  │ └─────────────────────────────────────────────────┘ │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ Cloud Run: unified-dashboard                         │ │
│  │ FastAPI serving UI + /api/* + /scheduler/*           │ │
│  │ Behind IAP (Identity-Aware Proxy)                    │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ Cloud Run: phase2-worker (optional)                  │ │
│  │ Blind TDD harness running on OpenRouter              │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ Cloud SQL: PostgreSQL (scheduler store, idea tree)   │ │
│  │ Cloud Scheduler: cron → /scheduler/dream-review      │ │
│  │ Secret Manager: API keys                             │ │
│  └─────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────┘
```

**Deploy command (adapted from travel-concierge):**

```bash
uv run python research_debate/deployment/deploy.py --create \
  --project_id=venturebot-hackathon \
  --location=us-central1 \
  --bucket=venturebot-artifacts
```

**Agent Engine App (adapted from cross-session-memory):**

```python
# research_debate/agent_engine_app.py
from vertexai.preview.reasoning_engines import AdkApp
from research_debate.agent import root_agent
from research_debate.memory.memory_config import memory_bank_config

app = AdkApp(
    agent=root_agent,
    enable_tracing=True,
)

# memory_bank_config is passed via context_spec in deploy.py,
# same as cross-session-memory/app/app_utils/deploy.py
```

---

## 11. Build Plan & Milestones

### 11.1 Milestone 1: Phase 1 MVP (Core Debate)

**Goal:** A user can input a vague idea and get a scored verdict + PRD.

| Task | Descope? | Est. | Key Reference |
|------|----------|------|---------------|
| 0. Environment | Install google-adk, verify Gemini key, pyproject.toml | 1h | `.env.example` |
| 1. Researcher agent | LlmAgent + google_search + clarify tool | 2h | `academic-research/agent.py`, `market-research-agent/agent.py` |
| 2. Advocate agent | Agent in chain, no tools | 1h | `llm-auditor/agent.py` |
| 3. Critic agent | Agent + google_search for fact-checking | 1.5h | `llm-auditor/critic/agent.py` |
| 4. Judge agent | Agent with structured output (response_schema) | 1.5h | `sdlc-task-planner/prompt.py` |
| 5. PRD Writer agent | LlmAgent synthesizing all inputs | 1.5h | `sdlc-technical-designer/prompt.py`, `sdlc-user-story-refiner/prompt.py` |
| 6. ADK wiring | SequentialAgent chain + root agent | 1h | `llm-auditor/agent.py`, `financial-advisor/agent.py` |
| 7. HITL gates | clarify tool + verdict gate + PRD approval | 2h | Long Horizon's `ask_parent` + `clarify` pattern |
| 8. Phase 2 bridge | Handoff approved PRD → blind TDD harness | 0.5h | Already built |
| **Total M1** | | **12h** | |

### 11.2 Milestone 2: Observable UI

**Goal:** Live dashboard with debate transcript, chat, idea tree, Kanban.

| Task | Descope? | Est. |
|------|----------|------|
| 9. SSE streaming | Agent messages → UI in real time | 2h |
| 10. Chat + HITL buttons | Chat input + approval/abort buttons | 1.5h |
| 11. Idea tree UI | Left panel showing ACTIVE/PARK/PRUNE | 1h |
| 12. Kanban + Phase 2 progress | Already partially built, extend | 1h |
| **Total M2** | | **5.5h** |

### 11.3 Milestone 3: Self-Improvement (Stage 1)

**Goal:** Agent gets better between runs. Dream review works on demand.

| Task | Descope? | Est. |
|------|----------|------|
| 13. SQLite memory store | Schema + CRUD for facts, lessons, ideas | 1.5h |
| 14. auto_capture | after_agent_callback → save session | 1h |
| 15. review_fork | Fire-and-forget analysis of each turn | 1.5h |
| 16. dream_review | Consolidation pass, triggered manually | 2h |
| 17. Technique library UI | Right panel showing learned techniques | 1h |
| **Total M3** | | **7h** |

### 11.4 Milestone 4: Shadow Mode + GCP Deploy

**Goal:** ADK coding agent tracks alongside custom, deployment ready.

| Task | Est. |
|------|------|
| 18. coder_shadow agent | ADK Agent translating Pi patterns | 3h |
| 19. Anti-degradation gate | Metrics collector + comparator | 1.5h |
| 20. GCP deploy (Agent Engine) | deploy.py + memory_bank_config | 2h |
| 21. GCP deploy (Cloud Run UI) | unified_dashboard as Cloud Run service | 1.5h |
| 22. Dream review scheduler | Cloud Scheduler → /scheduler/dream-review | 1h |
| 23. Demo script + polish | Walkthrough flow, error handling | 1.5h |
| **Total M4** | | **10.5h** |

**Total all milestones: ~35 hours** (approximately 2× the original 10h estimate,
driven by self-improvement + shadow mode + GCP deploy + UI which were not in scope).

---

## 12. ADK Sample Resources (Reuse Map)

### 12.1 Primary Patterns to Reuse (Copy Code From)

| What to reuse | Source file | Purpose |
|--------------|-------------|---------|
| `SequentialAgent` chain pattern | `python/agents/llm-auditor/llm_auditor/agent.py` | Advocate → Critic → Judge debate pipeline |
| Critic with `google_search` | `python/agents/llm-auditor/llm_auditor/sub_agents/critic/agent.py` | Exactly matches our Critic design |
| `after_model_callback` grounding | `python/agents/llm-auditor/llm_auditor/sub_agents/critic/agent.py` (`_render_reference`) | Append search references to responses |
| `AgentTool` for sub-agents | `python/agents/academic-research/academic_research/agent.py` | Root agent delegating to specialists |
| Orchestrator with parallel agents | `contrib/python/market-research-agent/app/agent.py` | Research agent dispatching to sub-agents |
| Structured output prompt format | `python/agents/sdlc-task-planner/sdlc_task_planner/prompt.py` | Template for Judge and PRD Writer output specs |
| `response_schema` enforcement | `python/agents/financial-advisor/financial_advisor/agent.py` | JSON schema enforcement on agents |
| ADK eval runner | `python/agents/travel-concierge/eval/test_eval.py` | Phase 1 eval infrastructure |
| Memory Bank wiring (2 lines) | `core/python/cross-session-memory/app/agent.py` | PreloadMemoryTool + after_agent_callback |
| Memory Bank config | `core/python/cross-session-memory/app/app_utils/memory_config.py` | Custom topics + managed topics |
| Agent Engine deploy | `python/agents/travel-concierge/deployment/deploy.py` | AdkApp + agent_engines.create |
| Agent Engine + Memory Bank deploy | `core/python/cross-session-memory/app/app_utils/deploy.py` | Deploy with Memory Bank enabled |

### 12.2 Patterns to Study (Not Copy, Learn From)

| What to study | Source | Why |
|--------------|--------|-----|
| Dream review algorithm | `core/python/long-horizon-harness/horizon/memory/dream_review.py` | Consolidation pass structure. We implement our own for SQLite but follow the same algorithm. |
| Review fork | `core/python/long-horizon-harness/horizon/memory/review_fork.py` | Fire-and-forget analysis pattern. |
| SiblingAgentPlugin | `core/python/long-horizon-harness/horizon/memory/sibling_agent_plugin.py` | Async fork management. |
| auto_capture | `core/python/long-horizon-harness/horizon/memory/auto_capture.py` | Throttled memory write-back. |
| Throttle mechanism | `core/python/long-horizon-harness/horizon/memory/_throttle.py` | Cooldown-based fork throttling. |
| 3-tier system prompt | `core/python/long-horizon-harness/horizon/conversation/system_prompt.py` | How to structure prompts with stable/volatile tiers. |
| Resumability | `core/python/long-horizon-harness/horizon/agent.py` (`ResumabilityConfig`) | How ADK sessions survive restarts. |
| A2A streaming | `core/python/long-horizon-harness/horizon/a2a/executor.py` | How to stream agent events to a UI. |
| Compaction | `core/python/long-horizon-harness/horizon/context/summarizer.py` | How to keep context windows manageable long-term. |
| User profile consolidation | `core/python/long-horizon-harness/horizon/memory/user_profile.py` | How to maintain a structured user profile across sessions. |
| HITL/resurfacing | `core/python/long-horizon-harness/horizon/subagents/delegate_runner.py` (`drive_child`) | How a sub-agent pauses for human approval and resumes. |
| HITL ask_parent | `core/python/long-horizon-harness/horizon/subagents/ask_parent.py` | Escalation pattern — child asks parent (human) for a decision. |

### 12.3 Don't Copy (Overkill for Our Scope)

| What | Why skip |
|------|----------|
| Per-user sandbox provisioning | VPS doesn't have Sandbox API; KISS for Stage 1 |
| Exfil guard / permission guard | Phase 1 has no shell tools; no exfil risk |
| OAuth / Connect Google | Not needed for hackathon demo |
| Cloud SQL with retry resilience | SQLite is fine for Stage 1; add when deploying |
| Vite React frontend | FastAPI + HTML + SSE is simpler and sufficient |
| Terraform infrastructure | VPS first, manual GCP setup for deploy |

---

## 13. Non-Functional Requirements

| ID | Requirement | How Verified |
|----|------------|--------------|
| NFR-1 | **Budget Control:** Per-run LLM cost ≤ $2.00. Rate limiter enforces. | E-05 eval case |
| NFR-2 | **Iteration Cap:** Phase 2 loops ≤ 5 iterations. Configurable via env. | Already implemented in `venturbot_harness.py` |
| NFR-3 | **Resumable:** A crashed run can be inspected via `state.json`. No loss. | Already implemented in `sim_store.py` |
| NFR-4 | **Idempotent Reset:** `POST /api/reset` always yields clean state. | Already implemented |
| NFR-5 | **Config-Driven:** Models, keys, paths, budgets from env, never hardcoded. | Config in `.env` + `config.py` |
| NFR-6 | **Non-Blocking Forks:** Self-improvement forks fire-and-forget. Never slow down the user response. | Long Horizon's `SiblingAgentPlugin` pattern |
| NFR-7 | **No-Self-Degradation:** Anti-degradation gate auto-reverts if metrics drop. | Stage 2 shadow mode metrics |
| NFR-8 | **Grounding:** Debate claims are cited (URLs to search results). | `after_model_callback` + `_render_reference` pattern |
| NFR-9 | **Observability:** Every agent thought is visible in UI. No black box. | SSE streaming to dashboard |
| NFR-10 | **Deterministic Budget:** Every LLM call has explicit timeout + max_tokens. | Configurable in `config.py` |

---

## Appendix A: GLOSSARY

| Term | Definition |
|------|-----------|
| **ADK** | Google Agent Development Kit — the Python framework for building agents |
| **Agent Engine** | Vertex AI managed service for deploying ADK agents |
| **A2A** | Agent-to-Agent protocol — how agents communicate |
| **Blind TDD** | Test-Driven Development where TestWriter never sees the implementation and Coder never sees the PRD |
| **Clarify** | HITL tool that pauses agent execution and asks the human a question |
| **Dream Review** | Nightly consolidation pass that processes recent sessions into improved agent memory |
| **HITL** | Human-in-the-Loop — points where the agent waits for human input |
| **IAP** | Identity-Aware Proxy — GCP's auth layer for web applications |
| **Idea Tree** | Hierarchical structure of research ideas with ACTIVE/PARK/PRUNE status |
| **Memory Bank** | Vertex AI managed service for cross-session memory |
| **OpenRouter** | Third-party LLM API aggregator (used for Phase 2 coding models) |
| **Shadow Mode** | Running two coding agents in parallel, comparing outputs, only using one |
| **SSE** | Server-Sent Events — unidirectional real-time streaming from server to browser |
| **SequentialAgent** | ADK pattern that runs sub-agents one after another, each receiving the prior's output |

---

## Appendix B: FILE MANIFEST

| File | Purpose | Status |
|------|---------|--------|
| `research_debate/agent.py` | Root ADK agent (SequentialAgent chain) | ⬜ TODO |
| `research_debate/sub_agents/researcher/agent.py` | Research Agent + google_search + clarify | ⬜ TODO |
| `research_debate/sub_agents/researcher/prompt.py` | Research Agent system prompt | ⬜ TODO |
| `research_debate/sub_agents/advocate/agent.py` | Advocate Agent (no tools) | ⬜ TODO |
| `research_debate/sub_agents/advocate/prompt.py` | Advocate system prompt | ⬜ TODO |
| `research_debate/sub_agents/critic/agent.py` | Critic Agent + google_search | ⬜ TODO |
| `research_debate/sub_agents/critic/prompt.py` | Critic system prompt | ⬜ TODO |
| `research_debate/sub_agents/judge/agent.py` | Judge Agent + response_schema | ⬜ TODO |
| `research_debate/sub_agents/judge/prompt.py` | Judge system prompt | ⬜ TODO |
| `research_debate/sub_agents/prd_writer/agent.py` | PRD Writer Agent | ⬜ TODO |
| `research_debate/sub_agents/prd_writer/prompt.py` | PRD Writer system prompt | ⬜ TODO |
| `research_debate/sub_agents/coder_shadow/agent.py` | Shadow ADK coding agent (Stage 2) | ⬜ TODO |
| `research_debate/tools/clarify.py` | HITL clarification tool | ⬜ TODO |
| `research_debate/tools/web_research.py` | Extended web research tools | ⬜ TODO |
| `research_debate/memory/sqlite_store.py` | SQLite memory store (Stage 1) | ⬜ TODO |
| `research_debate/memory/auto_capture.py` | Fork 1: per-turn save | ⬜ TODO |
| `research_debate/memory/review_fork.py` | Fork 2: per-turn analysis | ⬜ TODO |
| `research_debate/memory/dream_review.py` | Fork 3: nightly consolidation | ⬜ TODO |
| `research_debate/memory/idea_tree.py` | Idea tree CRUD + pruning | ⬜ TODO |
| `research_debate/memory/memory_config.py` | Memory Bank config (adapted from cross-session-memory) | ⬜ TODO |
| `research_debate/deployment/deploy.py` | GCP Agent Engine deploy (adapted from travel-concierge) | ⬜ TODO |
| `research_debate/agent_engine_app.py` | Agent Engine App wrapper | ⬜ TODO |
| `research_debate/pyproject.toml` | Python dependency spec | ⬜ TODO |
| `blind_tdd/config.py` | Phase 2 configuration | ✅ DONE |
| `blind_tdd/llm_client.py` | OpenRouter client | ✅ DONE |
| `blind_tdd/agents.py` | PO/TestWriter/Coder/QA_PO | ✅ DONE |
| `blind_tdd/venturbot_harness.py` | Blind TDD orchestrator | ✅ DONE |
| `blind_tdd/sim_store.py` | File-backed state store | ✅ DONE |
| `blind_tdd/dashboard.py` | FastAPI Phase 2 dashboard | ✅ DONE |
| `bridge.py` | Phase 1 → Phase 2 handoff | ⬜ TODO |
| `unified_dashboard.py` | Combined Phase 1+2+Self-Improve UI | ⬜ TODO |
| `scheduler.py` | Dream review cron trigger | ⬜ TODO |
| `shared_state.json` | Cross-phase shared state | ✅ EXISTS |

---

*This PRD is the complete specification for the VentureBot build. Every agent,
every tool, every metric, every deployment step is specified with a concrete
reference to an existing ADK sample that demonstrates the pattern. Build
begins at Milestone 1, Task 0: install google-adk and verify the Gemini API key.*