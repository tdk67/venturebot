# FL-13: Idea-02 — Full Pipeline Extension with Design Resolution

**Date:** 2026-08-18 (v2, post-discussion with Tamas)
**Status:** Design resolved. Ready to proceed with Phase 1 ADK build.
**Supersedes:** idea-01.md

---

## 0. Executive Summary

`idea-01.md` described a research agent with debate, web search, and memory loops.
The VentureBot prototype built so far covers **only Phase 2** (blind TDD from a PRD).
Phase 1 (research → debate → PRD → human approval) was forgotten.

This document:
1. Restores the full two-phase vision
2. Resolves all open design questions from the discussion with Tamas
3. Adds a **self-improvement loop** based on Long Horizon's `dream_review` pattern
4. Designs an **anti-degradation gate** for the ADK coding agent migration
5. Specifies the **observability UI** as a live conversation + pipeline interface

**The complete VentureBot pipeline:**

```
PHASE 1 (Google ADK)                          PHASE 2 (blind TDD)
┌───────────────────────────────┐      ┌───────────────────────────────┐
│ Vague Idea                    │      │ Approved PRD                  │
│   ↓                           │      │   ↓                           │
│ Research Agent → Resources    │      │ PO → TestWriter → pytest      │
│   ↓                           │      │   ↓                           │
│ Advocate → Critic → Judge     │      │ Coder → pytest → QA_PO       │
│   ↓                           │      │   ↓                           │
│ PRD Writer                    │      │ APPROVE/REVISE loop           │
│   ↓                           │      │   ↓                           │
│ Human Review ← Clarifications │      │ Working MVP                   │
│   ↓                           │      └───────────────────────────────┘
│ PRD Approved                  │
└───────────────────────────────┘
           │
           ▼
┌───────────────────────────────────────────────────────────────┐
│ SELF-IMPROVEMENT LAYER (cross-cut)                            │
│                                                                │
│ After every run: review_fork (what went well/wrong)            │
│ Periodic (nightly): dream_review (consolidate lessons, prune)  │
│ Continuous: memory preloading into agent context               │
└───────────────────────────────────────────────────────────────┘
```

---

## 1. Architecture Decision: Google ADK All The Way (Gradual Migration)

### 1.1 Current State

| Component | Stack | Status |
|-----------|-------|--------|
| Phase 2 (blind TDD) | Custom Python + OpenRouter | ✅ Working |
| Pi (me) | The proven code generator | ✅ Working horse |
| A2A mesh | Pisti ↔ Sisi ↔ Nori | ✅ Live |

### 1.2 Resolution: ADK Is Target, Pi Is Bootstrap

**Answer to Q1 (Pi's role):** Tamas's instinct was correct — using Pi agents with A2A
for the debate is **too heavy and too costly for the hackathon demo**. The target
architecture is **pure Google ADK** for Phase 1, with Phase 2 migrating to ADK
gradually under safety gates.

**The bootstrap path (3 stages):**

```
STAGE 1 (now)           STAGE 2 (safe migration)        STAGE 3 (target)
┌──────────────┐       ┌──────────────────────┐       ┌──────────────────────┐
│ Phase 1: ADK │       │ Phase 1: ADK          │       │ Phase 1: ADK          │
│ Phase 2:     │       │ Phase 2: SHADOW MODE  │       │ Phase 2: ADK          │
│ Custom+OR    │  ──▶  │  Primary: Custom+OR   │  ──▶  │  Primary: ADK         │
│              │       │  Shadow:  ADK (runs   │       │  Fallback: Custom+OR  │
│              │       │           but output  │       │                      │
│              │       │           is compared)│       │                      │
└──────────────┘       └──────────────────────┘       └──────────────────────┘
```

**Stage 2 — Shadow Mode (critical anti-degradation gate):**
- Both the ADK coding agent AND the proven OpenRouter one run the same PRD
- The ADK agent's output is compared to OpenRouter's across N runs (target: 10)
- Metrics compared: test pass rate, iteration count to convergence, code quality scores
- **ADK takes over as primary only if it consistently outperforms** (≥95% pass-rate parity)
- Custom+OpenRouter stays as a **hot fallback** forever — the escape hatch
- If EVER the ADK agent's pass rate drops below 80%, the system auto-reverts to custom+OR

### 1.3 Why This Matters for Hackathon Story

The hackathon wants to promote Google ADK. Having **both phases running on ADK**
is a stronger story. But the coding agent is the hardest part to get right — it's
where quality and cost meet. The shadow-mode migration path means we can:
- Demo Phase 1 on ADK for sure (it's the debate, ADK's strength)
- Show Phase 2 migration as the "advanced self-improvement story"
- Never risk a live demo crash because of an unproven coding agent

---

## 2. Phase 1: Research & Debate Pipeline (Google ADK)

### 2.1 Agent Graph

```
                    ┌─────────────┐
                    │  Human API  │  (observability UI, not Telegram v1)
                    └──────┬──────┘
                           │ vague idea + optional resource seeds
                           ▼
              ┌─────────────────────────┐
              │  Research Agent         │  Gemini 3.7 Flash
              │  tools: google_search,  │
              │  web_browse (optional)  │
              │                         │
              │  Gathers:               │
              │  - GitHub repos        │
              │  - Prior art / papers   │
              │  - Market signals       │
              │  - API/SDK docs         │
              │  Output: research_brief │
              └────────────┬────────────┘
                           │ research_brief
                           ▼
              ┌─────────────────────────┐
              │  Clarification Gate     │  HITL
              │  (if conflicting info   │
              │   or missing expertise) │
              │  Human responds → loop  │
              └────────────┬────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────┐
│  DEBATE PIPELINE (SequentialAgent)                  │
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │  ADVOCATE  (Gemini 3.7 Flash)               │   │
│  │  "Why this is great"                        │   │
│  │  - Market need evidence                     │   │
│  │  - Unique value proposition                 │   │
│  │  - Technical feasibility                    │   │
│  │  - Architecture proposal                    │   │
│  │  - Target MVP scope                         │   │
│  └──────────────────┬──────────────────────────┘   │
│                     │ advocate_argument              │
│                     ▼                                │
│  ┌─────────────────────────────────────────────┐   │
│  │  CRITIC  (Gemini 3.1 Pro, google_search)    │   │
│  │  "Why this will fail"                       │   │
│  │  - Challenge every assumption               │   │
│  │  - Cite prior art overlaps (web search)     │   │
│  │  - Identify failure modes, cost traps       │   │
│  │  - Challenge architecture choices           │   │
│  │  - Flag over-engineering / under-scoping    │   │
│  └──────────────────┬──────────────────────────┘   │
│                     │ advocate + critic arguments    │
│                     ▼                                │
│  ┌─────────────────────────────────────────────┐   │
│  │  JUDGE  (Gemini 3.1 Pro)                    │   │
│  │  Structured verdict:                        │   │
│  │  - Novelty score (1-10)                     │   │
│  │  - Feasibility score (1-10)                 │   │
│  │  - Market Fit score (1-10)                  │   │
│  │  - Overall: PROCEED / PARK / PRUNE          │   │
│  │  - Architecture decision record             │   │
│  └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
                           │
                           ▼
              ┌─────────────────────────┐
              │  Verdict Gate           │  HITL
              │  If any score < 6:      │
              │  Human [Abort] or       │
              │         [Proceed Anyway] │
              └────────────┬────────────┘
                           │
                           ▼
              ┌─────────────────────────┐
              │  PRD WRITER             │  Gemini 3.1 Pro
              │  Synthesizes:           │
              │  - Research brief       │
              │  - Debate arguments     │
              │  - Architecture record  │
              │  → Structured PRD       │
              └────────────┬────────────┘
                           │
                           ▼
              ┌─────────────────────────┐
              │  PRD Approval Gate      │  HITL
              │  [Approve] [Changes]    │
              │     [Reject]             │
              │  On Changes: feedback   │
              │  → loop to Research     │
              └────────────┬────────────┘
                           │ APPROVED
                           ▼
              ┌─────────────────────────┐
              │  PHASE 2: Blind TDD     │
              └─────────────────────────┘
```

### 2.2 Human-in-the-Loop Touchpoints (HITL)

Pi's `clarify` tool pattern (asking the human a question mid-turn and resuming
after the answer) is exactly what we need here. ADK has a built-in mechanism for
this via `long_running_operation` / `input_required` status messages in A2A.
Long Horizon uses `ask_parent` for escalation in sub-agents. We adapt this:

| Gate | Mechanism | What happens |
|------|-----------|-------------|
| Research Clarification | Agent emits `clarify(question)` → human answers in UI → agent resumes | Loop until agent has enough info |
| Verdict Gate | Judge scores + structured verdict shown in UI. Human clicks button. | [Proceed] → PRD Writer; [Abort] → archived |
| PRD Approval | PRD rendered in UI. Human clicks button. | [Approve] → Phase 2; [Changes] → prompt for feedback → loop; [Reject] → archived |
| Change Request | Human writes feedback in UI chat. | Feedback is prepended as context to Research Agent in the next loop iteration |

### 2.3 ADK Patterns We Reuse

From the `adk-samples` codebase at `/root/patchee-sandbox/adk-samples/`:

| Pattern | Source file | How used |
|---------|-------------|----------|
| `SequentialAgent` | `python/agents/llm-auditor/llm_auditor/agent.py` | Advocate → Critic → Judge chain |
| `Agent` with `google_search` | `python/agents/llm-auditor/llm_auditor/sub_agents/critic/agent.py` | Research Agent + Critic web search |
| `after_model_callback` + grounding | `python/agents/llm-auditor/llm_auditor/sub_agents/critic/agent.py` (`_render_reference`) | Append search references to Judge output |
| Sub-agent delegation | `python/agents/data-science/data_science/agent.py` | Root agent delegates to researcher, then debate chain |
| ADK eval suite | `python/agents/llm-auditor/eval/` | Our eval harness for Phase 1 |
| Deployment pattern | `python/agents/llm-auditor/deployment/deploy.py` | GCP Agent Engine deploy |
| After-turn memory write | `core/python/long-horizon-harness/horizon/memory/auto_capture.py` | Self-improvement: save session facts |
| Dream review | `core/python/long-horizon-harness/horizon/memory/dream_review.py` | Self-improvement: nightly profile consolidation |
| Review fork | `core/python/long-horizon-harness/horizon/memory/review_fork.py` | Self-improvement: per-turn what-went-well analysis |
| SiblingAgentPlugin | `core/python/long-horizon-harness/horizon/memory/sibling_agent_plugin.py` | Self-improvement: fire-and-forget async forks |

---

## 3. Self-Improvement Layer (THE Key Differentiator)

### 3.1 Why This Matters

> "The main promise of the solution is to provide a self-improving tool"
> — Tamas

This is VentureBot's moat. A debate agent that generates a PRD is interesting.
A coding agent that builds an MVP is useful. But an agent that **gets better at
both every cycle**, learns from its mistakes, prunes dead branches, and refines
its thinking process night after night — that's what nobody else is showing.

Long Horizon (`core/python/long-horizon-harness/`) has the reference
implementation. We adapt its 3-fork pattern:

### 3.2 Three-Fork Self-Improvement Loop

```
┌──────────────────────────────────────────────────────────────────┐
│  EVERY TURN (after the agent responds, non-blocking)             │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │ FORK 1: auto_capture (throttled, 120s cooldown)              ││
│  │                                                              ││
│  │ call_llm("Judge, review this turn. What did the agent do    ││
│  │           well? What did it do wrong? Save as memory.")      ││
│  │                                                              ││
│  │ Output → Memory Bank: "The agent should avoid using X when   ││
│  │           Y is available. Better to Z first."                ││
│  └──────────────────────────────────────────────────────────────┘│
│                                                                  │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │ FORK 2: review_fork (throttled, same cooldown)               ││
│  │                                                              ││
│  │ call_llm("Analyze the full debate transcript. Did the        ││
│  │           Advocate miss any arguments? Did the Critic        ││
│  │           overlook a weakness? What should be done            ││
│  │           differently? Is this idea worth keeping alive?")   ││
│  │                                                              ││
│  │ Output → Idea tree update: keep / park / prune               ││
│  │          + technique memory: "When debating architecture,    ││
│  │          always check database scaling cost early."          ││
│  └──────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  NIGHTLY (cron / scheduler, when the agent is "sleeping")        │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │ FORK 3: dream_review (consolidation pass)                    ││
│  │                                                              ││
│  │ 1. Load ALL recent sessions (last 24h)                       ││
│  │ 2. Extract: all memories, lessons, agent judgments           ││
│  │ 3. Consolidate (dedupe + resolve contradictions):            ││
│  │    - Structured User Profile: "Tamas prefers Python,         ││
│  │      uses OpenRouter, target is GCP deploy, hates YAML"     ││
│  │    - Consolidated agent lessons: top N most impactful        ││
│  │      improvements, merging similar ones, discarding          ││
│  │      contradictory ones with LLM-assisted resolution         ││
│  │ 4. Prune idea tree:                                          ││
│  │    - Ideas with score < 5 and no human intervention → PRUNE ││
│  │    - Ideas with stale research (no updates in 7 days) → PARK ││
│  │    - Ideas with rising scores → keep alive, notify human    ││
│  │ 5. Output: updated profile + pruned tree + improvement list ││
│  └──────────────────────────────────────────────────────────────┘│
│                                                                  │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │ PRE-COMPACTION FLUSH (from Long Horizon)                     ││
│  │                                                              ││
│  │ Before old turns are lossily summarized for context          ││
│  │ compaction, extract durable facts and save to Memory Bank    ││
│  │ so they survive summarization.                               ││
│  └──────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────┘
```

### 3.3 Pi's Tree Pruning → VentureBot's Idea Tree

Pi has a tree-pruning mechanism (pruning messages from context). VentureBot
extends this concept to **idea-tree pruning**:

```
Idea Tree (SQLite / Memory Bank):

  venturebot (root)
  ├── 🔴 PRUNED: AI email summarizer (score 2/10, prior art: 50+ products)
  ├── 🟡 PARKED: local LLM stock prediction (score 4/10, hardware infeasible)
  ├── 🟢 ACTIVE: AI PDF streaming reports (score 8/10, 3 competitor gaps identified)
  │   ├── research_brief_2026-08-18.json
  │   ├── debate_transcript_2026-08-18.json
  │   ├── prd_v2_approved.md
  │   └── mvp_workspace/
  └── 🟢 ACTIVE: self-improving research buddy (score 9/10, hackathon)
      ├── research_brief_2026-08-17.json
      ├── debate_transcript_2026-08-17.json
      ├── prd_v1_approved.md
      └── mvp_workspace/  (Phase 2 output)

Nightly dream_review re-evaluates all ACTIVE + PARKED nodes:
- Any PARKED node with new information → re-activate the debate
- Any ACTIVE node that's been stale → PARK
- Any ACTIVE node with dropped scores → notify human
```

### 3.4 The Retrospective Prompt

The "sleeping time" retrospective is a structured LLM call:

```
You are VentureBot's self-improvement engine. You run during the
nightly consolidation pass.

Review the user's recent sessions:

<session_1>
  Turn 1: [Human] "I want to build a tool for X"
  ...
  Turn 10: [Agent] "Here is the PRD. [Approve]?"
  Turn 11: [Human] "APPROVED"
</session_1>

<session_2>
  ...
</session_2>

<current_agent_memories>
  - "When researching, always check GitHub first"
  - "Critics should focus on cost, not aesthetics"
</current_agent_memories>

Analyze:

1. What patterns did the agent get right across sessions?
2. What patterns did the agent consistently get wrong?
3. What would have made each session 20% better?
4. Any contradictions in the agent's current memory? Resolve them.
5. Which ideas should be pruned, parked, or kept active?
6. What new agent technique should be added?
7. What existing technique should be removed or modified?

Output structured JSON:
{
  "consolidated_lessons": [...],
  "contradiction_resolutions": [...],
  "new_techniques": [...],
  "retired_techniques": [...],
  "idea_tree_changes": [...],
  "profile_updates": {...}
}
```

### 3.5 Memory Storage (When GCP + Memory Bank Not Yet Available)

For the VPS bootstrap (Stage 1), we use **file-backed SQLite** (as `idea-01.md`
originally suggested). Long Horizon uses Memory Bank on Vertex AI, but that
requires GCP deployment. Our migration path:

| Stage | Memory Store | Dream Review |
|-------|-------------|--------------|
| Stage 1 (VPS) | SQLite + JSON files | Manual trigger `/dream-review` |
| Stage 2 (VPS + GCP test) | SQLite (active), Memory Bank (evaluation) | Both paths, compare |
| Stage 3 (GCP) | Memory Bank (Vertex AI) | Nightly cron `/scheduler/dream-review` |

---

## 4. Observability UI (The Sophisticated Interface)

### 4.1 Design

> "We need a UI to see the debate, see how the PRD is translated to actionable
> tasks, and see real-time how the tasks are processed until the demo becomes
> available. The agent gets conversation with human via chat messages."

The existing dashboard (`:8080`, Kanban + debate feed) is the starting point.
It needs to grow into a **three-panel live observability interface**:

```
┌──────────────────────────────────────────────────────────────┐
│  VentureBot                                                    │
│  ┌──────────┬──────────────────────────┬───────────────────┐ │
│  │          │                          │                   │ │
│  │  LEFT    │  CENTER                  │  RIGHT            │ │
│  │          │                          │                   │ │
│  │  📋      │  🔬 RESEARCH & DEBATE    │  📊 SELF-IMPROVE  │ │
│  │  IDEA    │                          │                   │ │
│  │  TREE    │  ┌─────────────────────┐ │  "Dream review    │ │
│  │          │  │ Research Agent:     │ │   found 3 new     │ │
│  │  🟢 idea1│  │ Searching for prior │ │   techniques      │ │
│  │  🟢 idea2│  │ art on GitHub...    │ │   last night"     │ │
│  │  🟡 idea3│  │ Found: 23 repos     │ │                   │ │
│  │  🔴 idea4│  │                     │ │  📈 Improvements   │ │
│  │          │  │ ADVOCATE:           │ │  Week over week:  │ │
│  │          │  │ "This is unique..." │ │  Pass rate: 85%→92%│ │
│  │          │  │                     │ │  Iterations: 3.2→2.1│ │
│  │          │  │ CRITIC:             │ │                   │ │
│  │          │  │ "But X exists..."   │ │  🧠 Memory store  │ │
│  │          │  │                     │ │  47 lessons       │ │
│  │          │  │ JUDGE:              │ │  3 contradictions │ │
│  │          │  │ Novelty 8/10 ✅     │ │  resolved          │ │
│  │          │  │ Feasibility 7/10 ✅ │ │                   │ │
│  │          │  │ Market Fit 6/10 ⚠️  │ │                   │ │
│  │          │  │                     │ │                   │ │
│  │          │  │ [Proceed][Abort]    │ │                   │ │
│  │          │  └─────────────────────┘ │                   │ │
│  │          │                          │                   │ │
│  │          │  ┌─────────────────────┐ │                   │ │
│  │          │  │ PRD → IMPLEMENTATION│ │                   │ │
│  │          │  │                     │ │                   │ │
│  │          │  │ KANBAN:             │ │                   │ │
│  │          │  │ ✅ PO               │ │                   │ │
│  │          │  │ ✅ TestWriter       │ │                   │ │
│  │          │  │ 🔄 Coder (iter 3/5) │ │                   │ │
│  │          │  │ ⬜ QA_PO            │ │                   │ │
│  │          │  │                     │ │                   │ │
│  │          │  │ [■ ■ ■ ■ □] 80%    │ │                   │ │
│  │          │  └─────────────────────┘ │                   │ │
│  │          │                          │                   │ │
│  │          │  💬 CHAT (human ↔ agent) │                   │ │
│  │          │  ┌─────────────────────┐ │                   │ │
│  │          │  │ Human: approve PRD  │ │                   │ │
│  │          │  │ Agent: Phase 2      │ │                   │ │
│  │          │  │        started...   │ │                   │ │
│  │          │  │ Type message... [▶] │ │                   │ │
│  │          │  └─────────────────────┘ │                   │ │
│  └──────────┴──────────────────────────┴───────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 Key UI Features

| Feature | What it shows |
|---------|--------------|
| Idea Tree (left) | Active/parked/pruned ideas, click to expand details, scores |
| Live Debate Transcript (center top) | Scrollable, streaming, agent-colored messages, search references linked |
| Implementation Kanban (center bottom) | Phase 2 task board, progress bar, iterations |
| Chat (center bottom) | Human-in-the-loop conversation, approval buttons, clarification questions |
| Self-Improvement Console (right) | Dream review results, improvement trends, memory stats, technique list |

### 4.3 Implementation

- **Framework:** Same FastAPI + HTML as current dashboard (no React needed for v1)
- **Real-time:** SSE (Server-Sent Events) for streaming debate transcripts and implementation progress
- **Chat:** Simple WebSocket or SSE + POST
- **Phase 1 state:** Reads from ADK's session store or a shared state file
- **Phase 2 state:** Already works via `state.json` polling

---

## 5. Phase 2: Coding Agent Migration to ADK

### 5.1 The Risk

> "If we make a mistake and the coding agent does not work effectively, the
> whole self-improvement will not work, it might even degrade the agent."

This is the central risk. An underperforming coding agent creates bad code,
which creates bad test results, which the self-improvement loop learns from,
which degrades future coding performance — a **death spiral**.

### 5.2 The Anti-Degradation Architecture

```
                        ┌─────────────┐
                        │  PRD Input   │
                        └──────┬──────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
    ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
    │ PRIMARY     │  │ SHADOW      │  │ METRICS     │
    │ Custom+OR   │  │ ADK+Gemini  │  │ COLLECTOR   │
    │ (proven)    │  │ (migrating) │  │             │
    │             │  │             │  │ Compares:   │
    │ Output:     │  │ Output:     │  │ - Pass rate │
    │ code + test │  │ code + test │  │ - Iterations│
    │ results     │  │ results     │  │ - Code size │
    └──────┬──────┘  └──────┬──────┘  │ - Coverage  │
           │                │         └──────┬──────┘
           ▼                ▼                │
    ┌──────────────────────────────────────┐ │
    │  OUTPUT SELECTOR + GATE CONTROLLER   │◀┘
    │                                      │
    │  ┌─────────────────────────────────┐ │
    │  │ IF shadow mode (Stages 1-2):    │ │
    │  │   - Primary output → user       │ │
    │  │   - Shadow output → metrics     │ │
    │  │   - After N runs, evaluate      │ │
    │  │                                 │ │
    │  │ IF shadow >= primary (95%):     │ │
    │  │   → PROMOTE: shadow becomes     │ │
    │  │     primary, old primary        │ │
    │  │     becomes hot fallback        │ │
    │  │                                 │ │
    │  │ IF primary drops below 80%:     │ │
    │  │   → AUTO-REVERT: fallback       │ │
    │  │     takes over immediately      │ │
    │  └─────────────────────────────────┘ │
    └──────────────────────────────────────┘
```

### 5.3 Metrics That Gate Migration

| Metric | Threshold to promote | Threshold to revert |
|--------|---------------------|---------------------|
| Test pass rate | ≥95% of primary's pass rate | <80% absolute |
| Iterations to converge | ≤ primary's average + 0 | > primary's average + 3 |
| Code quality score* | ≥ primary's score | < primary's score - 20% |
| Cost per run | ≤ primary's cost | > 2× primary's cost |

*Code quality score: LLM-as-judge scoring the generated code against a
rubric (correctness, readability, extensibility, edge-case handling).

### 5.4 What Pi Brings to the ADK Coding Agent

The ADK coding agent doesn't start from zero. We study Pi's approach and
implement the key ideas in ADK:

| Pi pattern | How to implement in ADK |
|-----------|------------------------|
| Tool-giving (read/write/bash) | ADK tools: `sandbox.terminal`, `sandbox.file_ops` |
| Tree-pruning context management | ADK `EventsCompactionConfig` + Long Horizon's `HorizonSummarizer` |
| Multi-turn reasoning | ADK `Runner` with resumable sessions |
| bash tool output parsing | ADK `after_tool_callback` to structure tool output |
| Error recovery (retry on LLM error) | ADK's built-in retry + our custom retry wrapper |
| Structured output enforcement | ADK's `response_schema` + `after_model_callback` parse guard |

---

## 6. Tool: Clarify (Human Clarification Request)

### 6.1 The Pattern

Pi has a `clarify` tool that pauses execution and asks the human a question.
This is exactly what Phase 1 needs when the Research Agent finds contradictory
information or needs domain expertise.

### 6.2 ADK Implementation

Long Horizon uses `ask_parent` in sub-agents for this. We implement a
`clarify(question)` tool on the Research Agent:

```python
def clarify(question: str, tool_context: ToolContext) -> str:
    """Ask the human a clarifying question and wait for their response."""
    # ADK's HITL mechanism: set the session state to "awaiting_clarification"
    session = tool_context.state
    session["clarify_question"] = question
    session["clarify_state"] = "awaiting_response"
    # The ADK runner will emit an "input-required" status,
    # the UI will show the question, and the human's next message
    # will be routed back to the agent at this exact point.
    return "Waiting for human response..."
```

The UI renders this as a styled question card with a text input → the
human's answer is injected as the function response for `clarify()`, and
the agent resumes its turn.

---

## 7. Project Structure

```
venturebot/
├── research_debate/                    # Phase 1 — Google ADK
│   ├── __init__.py                     # env loading, GOOGLE_API_KEY
│   ├── agent.py                        # root_agent (SequentialAgent chain)
│   ├── sub_agents/
│   │   ├── researcher/
│   │   │   ├── agent.py                # Agent with google_search + clarify
│   │   │   └── prompt.py
│   │   ├── advocate/
│   │   │   ├── agent.py
│   │   │   └── prompt.py
│   │   ├── critic/
│   │   │   ├── agent.py                # Critic with google_search for fact-check
│   │   │   └── prompt.py
│   │   ├── judge/
│   │   │   ├── agent.py
│   │   │   └── prompt.py
│   │   ├── prd_writer/
│   │   │   ├── agent.py
│   │   │   └── prompt.py
│   │   └── coder_shadow/               # Stage 3 ADK coding agent
│   │       ├── agent.py
│   │       └── prompt.py
│   ├── tools/
│   │   ├── web_research.py             # GitHub, ProductHunt, etc.
│   │   └── clarify.py                  # HITL clarification tool
│   ├── memory/                         # Self-improvement layer
│   │   ├── sqlite_store.py             # Stage 1-2 memory store
│   │   ├── auto_capture.py             # Fork 1: per-turn save
│   │   ├── review_fork.py              # Fork 2: per-turn analysis
│   │   ├── dream_review.py             # Fork 3: nightly consolidation
│   │   └── idea_tree.py                # Idea tree with pruning
│   ├── evaluation/
│   │   ├── eval_cases.py               # idea-01.md's 5 eval cases
│   │   └── anti_degradation.py         # Shadow mode metrics collector
│   ├── deployment/
│   │   └── deploy.py                   # GCP Agent Engine deployment
│   └── pyproject.toml
├── blind_tdd/                          # Phase 2 — Custom Python (Stage 1 primary)
│   ├── config.py
│   ├── llm_client.py                   # OpenRouter wrapper
│   ├── agents.py                       # PO, TestWriter, Coder, QA_PO
│   ├── venturebot_harness.py           # Blind TDD orchestrator
│   ├── sim_store.py                    # File-backed state
│   └── dashboard.py                    # FastAPI + HTML UI
├── bridge.py                           # Phase 1 → Phase 2 handoff
├── shared_state.json                   # Shared state
└── docs/
    ├── idea-01.md                      # Original vision
    └── idea-02.md                      # This document
```

---

## 8. Build Plan (Stages)

### Stage 1: Bootstrap (target: working VPS demo)
**Timeline:** ~12-15 hours build time

| # | Task | Hours | Depends on |
|---|------|-------|-----------|
| 0 | Install `google-adk`, verify Gemini API key, create pyproject.toml | 1.0 | — |
| 1 | Build `researcher` agent (google_search + clarify tool) | 2.0 | 0 |
| 2 | Build debate pipeline (Advocate + Critic + Judge via SequentialAgent) | 3.0 | 1 |
| 3 | Build `prd_writer` agent (structured PRD output) | 1.5 | 2 |
| 4 | Build HITL gates (clarify, verdict gate, PRD approval) | 1.5 | 1,2,3 |
| 5 | Build bridge (Phase 1 PRD → Phase 2 harness trigger) | 1.0 | 3,4 |
| 6 | Extend dashboard UI (debate transcript, chat, idea tree) | 2.0 | 5 |
| 7 | Build self-improvement layer (auto_capture + review_fork + SQLite) | 2.0 | 6 |
| 8 | Eval suite (run idea-01.md 5 cases + record) | 1.0 | 2,3 |
| **Total Stage 1** | | **15.0** | |

### Stage 2: Shadow Migration (target: 10-run comparison)
**Timeline:** ~8 hours (+ shadow eval time)

| # | Task | Hours |
|---|------|-------|
| 9 | Build `coder_shadow` ADK agent (Pi patterns translated to ADK) | 4.0 |
| 10 | Build anti-degradation metrics collector + gate controller | 2.0 |
| 11 | Run shadow mode for 10 PRDs, collect metrics, decide promote/revert | 2.0 |

### Stage 3: GCP Deploy (target: hackathon-ready)
**Timeline:** ~6 hours

| # | Task | Hours |
|---|------|-------|
| 12 | Set up GCP project, enable APIs, configure ADC | 1.0 |
| 13 | Deploy Phase 1 to Agent Engine + Cloud Run | 2.0 |
| 14 | Deploy Phase 2 to Cloud Run (with fallback wiring) | 1.5 |
| 15 | Deploy self-improvement layer (Cloud SQL + Memory Bank) | 1.5 |
| 16 | IAP + custom domain + demo script | 1.0 |

### Stage 4: Dream Review (target: live self-improvement)
**Timeline:** ~5 hours

| # | Task | Hours |
|---|------|-------|
| 17 | Build dream_review nightly consolidation pass | 2.0 |
| 18 | Wire Cloud Scheduler cron → dream_review endpoint | 1.0 |
| 19 | Build self-improvement console in UI (metrics, trends, technique library) | 2.0 |

**Total all stages: ~34 hours** (not 10×; about 2× the original 10h estimate
when you include self-improvement, GCP deploy, and shadow migration).

---

## 9. Eval Suite (from idea-01.md, adapted)

| ID | Test Input | Expected Flow | Pass Criteria |
|----|-----------|---------------|---------------|
| E-01 | "Build an AI email summarizer" | Judge finds 50+ products, scores Novelty 2/10 | Outcome: `PRUNE`, logged to idea tree |
| E-02 | "Stock prediction on Raspberry Pi with 99% accuracy" | Critic flags hardware impossibility, Feasibility 1/10 | Outcome: `PRUNE`, clear technical rationale |
| E-03 | "Long-Horizon Research Buddy with debate + self-improvement" | Advocate highlights hackathon relevance, Critic flags scope, Judge scores high | Outcome: `BUILD`, Novelty ≥ 8, Feasibility ≥ 7 |
| E-04 | "Something with AI and PDF reporting" (vague) | `clarify()` fires, human answers, Advocate proposes 3 niches, Critic eliminates basics, Judge picks best | Outcome: Enriched niche proposal returned |
| E-05 | Daily budget limit breach ($2.00 cap) | Rate limiter interceptor blocks, graceful halt | Outcome: Agent halts, alerts UI, state saved |

**Additional eval cases for self-improvement:**

| ID | Test Input | Expected Flow |
|----|-----------|---------------|
| E-06 | Run same PRD 3 times, check if agent improves | auto_capture learns from failures, iteration 3 converges faster than iteration 1 |
| E-07 | Nightly dream_review after 10 sessions | Profile consolidates correctly, contradictions resolved, dead ideas pruned |
| E-08 | Shadow mode comparison over 10 PRDs | ADK coding agent metrics vs. custom+OR metrics, gate decision is correct |

---

## 10. Environment & Setup

### 10.1 Required API Keys (.env)

```ini
# Gemini API (Tamas will provide)
GOOGLE_API_KEY=...

# OpenRouter (for Phase 2 — already works via ~/.pi/agent/auth.json)
OPENROUTER_API_KEY=sk-or-...

# Tavily or DuckDuckGo for research (if google_search not enough)
TAVILY_API_KEY=...

# VPS-local settings
VENTUREBOT_WORKSPACE=/root/venturebot/workspace
VENTUREBOT_STATE=/root/venturebot/state.json
VENTUREBOT_MAX_ITERATIONS=5
```

### 10.2 Python Dependencies

```toml
# pyproject.toml (Phase 1)
[project]
dependencies = [
    "google-adk>=1.0.0",
    "google-genai>=0.8.0",
    "google-adk[tools]",       # for google_search
    "fastapi",
    "uvicorn",
    "httpx",
    "pytest",
]

# Phase 2 dependencies (already in venv)
# fastapi, uvicorn, httpx, pytest
```

---

## 11. Final Architecture Summary

```
                    ┌──────────────────────────────────┐
                    │  HUMAN (Observability UI :8080)   │
                    │  Chat, Idea Tree, Kanban, Impr.  │
                    └──────────┬──────────┬────────────┘
                               │ SSE      │ POST (chat, buttons)
                    ┌──────────▼──────────▼────────────┐
                    │  UNIFIED DASHBOARD (FastAPI)      │
                    │  Serves UI, routes to agents      │
                    └──┬──────────┬──────────┬─────────┘
                       │          │          │
              ┌────────▼──┐ ┌─────▼──────┐ ┌─▼─────────┐
              │ PHASE 1   │ │ PHASE 2    │ │ SELF-     │
              │ ADK Agent │ │ Blind TDD  │ │ IMPROVE   │
              │           │ │            │ │           │
              │ Research  │ │ PO→TestWr  │ │ Forks 1-3 │
              │ Advocate  │ │ →Coder     │ │ Dream     │
              │ Critic    │ │ →QA_PO     │ │ Review    │
              │ Judge     │ │            │ │ Idea Tree │
              │ PRD Writer│ │            │ │           │
              └─────┬─────┘ └──────┬─────┘ └─────┬─────┘
                    │              │              │
                    ▼              ▼              ▼
              ┌──────────────────────────────────────┐
              │         SHARED STATE / MEMORY        │
              │  Stage 1: SQLite + shared_state.json │
              │  Stage 3: Memory Bank (Vertex AI)    │
              └──────────────────────────────────────┘
```

---

*This document is the definitive specification for VentureBot. All design
questions are resolved. Build begins with Stage 1, Task 0: install google-adk
and verify the Gemini API key.*