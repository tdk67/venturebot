# VentureBot — Loop Architecture v2

**Date:** 2026-08-20
**Status:** Design — ready for implementation discussion
**Supersedes:** notes/LOOP_ARCHITECTURE_ANALYSIS.md (v1 — single-root-agent approach, rejected)

---

## 1. What We Keep (The Good Parts)

The current debate structure is the right foundation:

| Agent | Model | Tools | Why distinct |
|-------|-------|-------|-------------|
| **Researcher** | Gemini 3.7 Flash | google_search, clarify | Gathers facts |
| **Advocate** | Gemini 3.7 Flash | None (blind) | Builds the case FOR |
| **Critic** | Gemini 3.1 Pro | google_search | Tears it apart WITH search |
| **Creative** | Gemini 3.7 Flash (hot) | None (blind) | Divergent niche hunting |
| **Judge** | Gemini 3.1 Pro | None | Structured verdict |
| **PRD Writer** | Gemini 3.1 Pro | None | Synthesizes PRD |
| **Security Auditor** | Gemini 3.1 Pro | None | Proof-reads PRD |

The **Advocate/Critic asymmetry** (Advocate is blind, Critic has web search) is a real architectural insight — it eliminates single-model confirmation bias. This must stay.

The **Creative head** running at high temperature is another keeper — it hunts niches the precise agents can't see.

---

## 2. What Must Change: From Sequential DAG to Agentic Loop

### 2.1 The Orchestrator Agent (new)

We add an **Orchestrator agent** that DRIVES the other agents. It does not replace them — it decides WHEN to call whom, in what order, and whether to loop back.

```
ORCHESTRATOR (Gemini 3.1 Pro, no web search)
  │
  │  Tools (function tools, not AgentTool — each wraps a sub-agent):
  │
  ├── research(idea, urls?) → research_brief
  ├── advocate(brief) → argument
  ├── critic(brief, argument) → rebuttal
  ├── creative(brief, argument, rebuttal) → angles
  ├── judge(brief, argument, rebuttal, angles) → verdict_json
  ├── write_prd(brief, argument, rebuttal, angles, verdict) → prd
  ├── audit(prd, brief) → audit_json
  │
  │  Loop tools:
  ├── clarify(question, choices?) → human_answer
  ├── read_file(path) → contents        (re-read own PRD before revising)
  ├── write_file(path, content) → ok     (write research brief, PRD, notes)
  │
  │  Memory tools:
  └── load_memories() → past_lessons     (READ past lessons before starting)
```

### 2.2 The Orchestrator's System Prompt

```
You are VentureBot's Orchestrator. Your job is to evaluate a startup idea
through a rigorous multi-agent engineering process. You don't debate
yourself — you delegate to specialized sub-agents, each with a different
perspective and (where appropriate) different information access.

YOUR PROCESS:

1. LOAD PAST LESSONS — Before anything else, call load_memories() to
   retrieve what VentureBot learned from previous runs. Apply ALL of these.
   If a past lesson says "always run security audit before presenting",
   you MUST run the audit.

2. RESEARCH — Call research(idea) to get a structured brief with prior
   art, market signals, technical landscape, and resource links.

3. CLARIFY — If the idea is vague, or the research reveals contradictory
   information, or you need domain expertise the user might have, call
   clarify(question). Wait for the answer. Then re-research with the new
   information. You may clarify multiple times.

4. DEBATE — Call advocate(brief), then critic(brief, argument), then
   creative(brief, argument, rebuttal). The Advocate is BLIND (no search)
   — it argues from the brief alone. The Critic HAS web search — it finds
   counter-evidence. The Creative finds niches and pivots.

5. JUDGE — Call judge(brief, argument, rebuttal, angles). It returns a
   structured verdict with scores.

6. VERDICT GATE:
   - PROCEED (avg ≥7): continue to PRD
   - PARK (4-6): ask human via clarify whether to continue
   - PRUNE (<4): present findings + recommend abandonment

7. DRAFT PRD — Call write_prd(brief, argument, rebuttal, angles, verdict).
   Saves PRD to workspace as PRD.md.

8. SELF-REVIEW — Call read_file("PRD.md"). Check it yourself:
   - Every section present? (Overview, FRs, NFRs, Architecture, Acceptance Criteria, Milestones)
   - Every claim cited? No unsupported assertions?
   - Security/auth/data-handling covered? Error handling? Rate limiting?
   - If gaps found → call write_prd again with instructions to fix them.
     Do NOT present an incomplete PRD to the human.

9. AUDIT — Call audit(prd, brief). It returns PASS or FLAG with findings.
   If FLAG → tell write_prd to fix the flagged issues, then re-audit.

10. PRESENT — Call clarify with the PRD summary and [Approve] [Changes] [Reject].
    If Changes → go back to RESEARCH with the human's feedback.
    If Reject → archive and stop.

STOPPING RULES:
- Stop when human approves the PRD
- Stop when human says abort/reject
- Stop when the same agent returns the same result 3 times in a row
  (you're looping without progress)
- Stop when you've consumed your iteration budget (50 turns)

WORKSPACE DISCIPLINE:
- Write all artifacts to the workspace (RESEARCH_BRIEF.md, PRD.md)
- Re-read files before editing them — never edit from memory
- Save final PRD with artifact(action='save')

PAST LESSONS (loaded via load_memories()):
{must_read_before_starting}
```

### 2.3 How the Orchestrator Loops

The key difference from the current pipeline: the Orchestrator **decides what to do next** based on what it sees. It's not forced through a phase counter.

Example trace:

```
Turn 1:  Orchestrator: load_memories()
         → Returns: "Rule: always verify market size claims with google_search.
                    Rule: check security/NFRs before presenting PRD.
                    Avoid: presenting PRDs without acceptance criteria."

Turn 2:  Orchestrator: research("Build an AI email summarizer for lawyers")
         → Returns research_brief with 50+ competitors, but legal niche is open

Turn 3:  Orchestrator: clarify("This space has 50+ products but the legal
         niche is underserved. Is 'lawyers who bill by the hour' the right
         target? Or do you have a different niche in mind?")
         [HUMAN ANSWERS: "Yes, lawyers. Specifically solo/small firm lawyers
          who can't afford enterprise tools."]

Turn 4:  Orchestrator: research("AI email summarizer for solo law firms")
         → Returns refined brief with compliance requirements, pricing sweet spot

Turn 5:  Orchestrator: advocate(brief)
         → Returns strong argument for legal niche

Turn 6:  Orchestrator: critic(brief, argument)
         → Finds: Clio and MyCase have basic AI features, but no dedicated
           email summarizer. ABA ethics rules require human review.

Turn 7:  Orchestrator: creative(brief, argument, rebuttal)
         → Suggests: "Time-saved-per-email" metric as unique selling point.
           Integration with Clio/MyCase as distribution channel.

Turn 8:  Orchestrator: judge(brief, argument, rebuttal, angles)
         → Returns: Novelty 8, Feasibility 7, Market Fit 8 → PROCEED

Turn 9:  Orchestrator: write_prd(brief, argument, rebuttal, angles, verdict)
         → Returns PRD

Turn 10: Orchestrator: read_file("PRD.md")
         → Self-reviews. Finds: "Missing: data retention policy for
           attorney-client privilege. Missing: acceptance criteria for
           FR-3 (Clio integration)."

Turn 11: Orchestrator: write_prd(brief, argument, rebuttal, angles, verdict,
          instructions="Add data retention policy (attorney-client privilege),
          add acceptance criteria for FR-3, add error handling for API failures")
         → Returns revised PRD

Turn 12: Orchestrator: read_file("PRD.md")
         → Self-reviews. All sections present. Ready for audit.

Turn 13: Orchestrator: audit(prd, brief)
         → Returns: PASS (no findings)

Turn 14: Orchestrator: clarify("PRD ready. Scores: N8 F7 M8. [Approve] [Changes] [Reject]?")
         [HUMAN CLICKS APPROVE]

Done in 14 turns with 2 human touchpoints (clarify + approve).
The PRD went through TWO revisions based on self-review.
```

Compare to the current pipeline: 7 fixed LLM calls, 0 revision, clarify doesn't loop.

---

## 3. Self-Improvement: The Missing Feedback Loop

### 3.1 What We Have Today

The three forks are implemented but operate **across runs only**:
- `auto_capture` — saves turn transcripts to SQLite (throttled 120s)
- `review_fork` — analyzes one turn, proposes lessons (fire-and-forget)
- `dream_review` — nightly consolidation + idea-tree pruning

**Critical gap:** Nothing READS these memories during a run. The pipeline never calls `get_lessons()` or `get_techniques()`. The orchestrator's `load_memories()` tool bridges this gap.

### 3.2 The Full Self-Improvement Loop

```
┌─────────────────────────────────────────────────────────────────┐
│  PRE-FLIGHT (every run)                                         │
│                                                                 │
│  load_memories() → returns:                                     │
│    - Active techniques: "always google_search market size"      │
│    - Avoid lessons: "don't skip security audit"                 │
│    - Reinforce lessons: "legal niche approach worked well"      │
│                                                                 │
│  These are injected into the orchestrator's context and         │
│  MUST be applied. The system prompt says: "Apply ALL lessons."  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  DURING RUN (after every sub-agent call)                        │
│                                                                 │
│  The orchestrator's output + sub-agent result is captured       │
│  by auto_capture (already working).                             │
│                                                                 │
│  The review_fork analyzes: "Did the orchestrator forget          │
│  something? Did it skip a lesson? Did it make the same          │
│  mistake again?" → proposes new lessons / retires stale ones.   │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  POST-RUN (human feedback integration)                          │
│                                                                 │
│  When the human says "you forgot the security check" or         │
│  "you didn't consider data privacy", this is NOT just a         │
│  one-time correction — it becomes a LESSON.                     │
│                                                                 │
│  The dashboard's "Changes" path calls:                          │
│    store.save_lesson("avoid", "forgot security check — user      │
│                      flagged it", "human_feedback")             │
│                                                                 │
│  Next run, load_memories() returns this lesson and the          │
│  orchestrator MUST run the audit before presenting.             │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  NIGHTLY (dream_review — already working)                       │
│                                                                 │
│  1. Collects all session facts from last 24h                    │
│  2. Consolidates similar lessons (dedup)                        │
│  3. Resolves contradictions (LLM-assisted)                      │
│  4. Prunes idea tree (deterministic rules)                      │
│  5. Curates techniques: promote effective ones, retire stale    │
│                                                                 │
│  Example: if 3 runs all produced "missing security audit"       │
│  lessons, dream_review merges them into one strong rule:        │
│  "ALWAYS run security audit before presenting PRD to human"     │
└─────────────────────────────────────────────────────────────────┘
```

### 3.3 The Human Feedback → Lesson Pipeline

This is the key new mechanism. When the human provides feedback (via the "Changes" button or chat), it's captured as a structured lesson:

```
Human feedback: "You forgot the security check. Also, you didn't
                consider GDPR compliance for EU users."

↓ (dashboard.py → review_fork pathway)

JSON stored in agent_lessons:
{
  "name": "human_feedback_2026-08-20_001",
  "rule": "ALWAYS: (1) Run security audit before presenting PRD,
           (2) Check GDPR/data-privacy compliance if target market
           includes EU users. Flagged by human on 2026-08-20.",
  "evidence": "human_feedback",
  "created_at": 1755734400.0
}

↓ (next run starts)

load_memories() → returns this lesson → orchestrator's system prompt
says "Apply ALL lessons" → orchestrator MUST call audit() and add
GDPR check before presenting.
```

### 3.4 Contradiction Resolution (dream_review enhancement)

Sometimes lessons contradict each other. The dream_review LLM prompt already handles this, but we need better structure:

```
Example contradiction:
  Lesson A: "Always google_search to verify Advocate's market-size claims"
  Lesson B: "Advocate is blind by design — don't give it search access"

Resolution (by dream_review):
  Keep both — they're about different agents. Refine:
  Lesson A': "When the ORCHESTRATOR reads the Advocate's argument,
            it should google_search any unsourced market-size claims
            before passing them to the Judge"
  Lesson B: unchanged (the Advocate agent stays blind)
```

---

## 4. Architecture Diagram (Updated)

```
┌──────────────────────────────────────────────────────────────────┐
│  HUMAN (Dashboard UI :8080)                                       │
│  Chat · Idea Tree · Kanban · Self-Improvement Console             │
└──────────┬──────────────┬───────────────┬────────────────────────┘
           │ SSE          │ POST (chat)   │ POST (buttons)
           ▼              ▼               ▼
┌──────────────────────────────────────────────────────────────────┐
│  FASTAPI DASHBOARD (dashboard.py)                                 │
│  Routes: /api/run, /api/resume, /api/steer, /api/clarify,        │
│          /api/dream-review, /api/memories                         │
└──────────┬───────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────────────────────┐
│  ORCHESTRATOR AGENT (new — orchestrator.py)                      │
│                                                                   │
│  Model: Gemini 3.1 Pro                                            │
│  System prompt: VentureBot engineering process                    │
│  Iteration budget: 50 turns (IterationBudgetPlugin)               │
│                                                                   │
│  Tools (function tools wrapping sub-agents):                      │
│  ┌─────────────┐ ┌──────────┐ ┌─────────┐ ┌──────────┐          │
│  │ research()  │ │advocate()│ │critic() │ │creative()│          │
│  │ (flash)     │ │ (flash)  │ │ (pro)   │ │ (flash,  │          │
│  │ +search     │ │ blind    │ │ +search │ │  hot)    │          │
│  └─────────────┘ └──────────┘ └─────────┘ └──────────┘          │
│  ┌─────────┐ ┌──────────┐ ┌─────────┐ ┌──────────────┐          │
│  │ judge() │ │write_prd()│ │ audit() │ │  clarify()   │          │
│  │ (pro)   │ │  (pro)    │ │ (pro)   │ │  (HITL)      │          │
│  └─────────┘ └──────────┘ └─────────┘ └──────────────┘          │
│  ┌──────────────┐ ┌──────────┐ ┌──────────┐                     │
│  │load_memories()│ │read_file │ │write_file│                     │
│  │              │ │          │ │          │                      │
│  └──────────────┘ └──────────┘ └──────────┘                     │
└──────────────────────────────────────────────────────────────────┘
           │                              │
           ▼                              ▼
┌──────────────────────┐    ┌──────────────────────────────────────┐
│  SUB-AGENT POOL      │    │  SELF-IMPROVEMENT LAYER              │
│  (agents/agents.py)  │    │                                      │
│                      │    │  ┌────────────────────────────────┐  │
│  Each is an ADK      │    │  │ PRE-FLIGHT: load_memories()    │  │
│  LlmAgent with its   │    │  │ reads agent_lessons +          │  │
│  own model, tools,   │    │  │ agent_techniques from SQLite   │  │
│  and temperature.    │    │  └────────────────────────────────┘  │
│                      │    │                                      │
│  Researcher          │    │  ┌────────────────────────────────┐  │
│  Advocate            │    │  │ DURING RUN: auto_capture +     │  │
│  Critic              │    │  │ review_fork (fire-and-forget,  │  │
│  Creative            │    │  │ throttled 120s)                │  │
│  Judge               │    │  └────────────────────────────────┘  │
│  PRD Writer          │    │                                      │
│  Auditor             │    │  ┌────────────────────────────────┐  │
│                      │    │  │ POST-RUN: human feedback →     │  │
│                      │    │  │ save_lesson("avoid", ...)      │  │
│                      │    │  └────────────────────────────────┘  │
│                      │    │                                      │
│                      │    │  ┌────────────────────────────────┐  │
│                      │    │  │ NIGHTLY: dream_review          │  │
│                      │    │  │ consolidate, resolve contra-   │  │
│                      │    │  │ dictions, prune idea tree,     │  │
│                      │    │  │ curate techniques              │  │
│                      │    │  └────────────────────────────────┘  │
└──────────────────────┘    └──────────────────────────────────────┘
```

---

## 5. Implementation Plan

### Phase A: Orchestrator + Sub-Agent Tools (replaces pipeline.py)

| Task | File | Effort |
|------|------|--------|
| A1. Build `orchestrator.py` — single ADK Agent with system prompt + iteration budget | `src/agents/orchestrator.py` | 3h |
| A2. Wrap each sub-agent as a function tool | `src/agents/orchestrator_tools.py` | 2h |
| A3. Add `load_memories()` tool that reads SQLite lessons/techniques | `src/agents/orchestrator_tools.py` | 1h |
| A4. Add `clarify()` as real HITL tool (harness pattern) | `src/agents/clarify.py` (rewrite) | 2h |
| A5. Wire orchestrator into `dashboard.py` (replace `run_debate` calls) | `src/dashboard.py` | 1h |
| A6. Add `read_file`/`write_file` tools for workspace artifacts | `src/agents/orchestrator_tools.py` | 1h |

### Phase B: Self-Improvement Feedback Loop

| Task | File | Effort |
|------|------|--------|
| B1. Human feedback → lesson: when user clicks "Changes" with text | `src/dashboard.py`, `src/memory/sqlite_store.py` | 1h |
| B2. Teach `review_fork` to detect "orchestrator forgot a known lesson" | `src/memory/review_fork.py` | 2h |
| B3. Teach `dream_review` to merge duplicate lessons + resolve contradictions | `src/memory/dream_review.py` | 2h |
| B4. Add self-improvement metrics to dashboard (lessons count, contradiction count, technique success rate) | `src/dashboard.py`, `templates/` | 1.5h |
| B5. Add `/api/memories` endpoint for the dashboard to display current lessons | `src/dashboard.py` | 0.5h |

### Phase C: Loop Quality

| Task | File | Effort |
|------|------|--------|
| C1. Add `IterationBudgetPlugin` (adopt harness pattern) | `src/agents/orchestrator.py` | 1h |
| C2. Add "no progress in 3 turns" halt guard | `src/agents/orchestrator.py` | 1h |
| C3. Add `GuardrailsPlugin` (exfil guard, repeated failure guard) | `src/guard.py` | 1h |
| C4. Eval suite: run 5 test ideas, measure: turns to PRD, revisions before approval, human corrections needed | `tests/` | 2h |

**Total Phase A+B+C: ~22 hours**

### Phase D: Deploy + Polish (later)

| Task | Effort |
|------|--------|
| D1. GCP Agent Engine deployment | 2h |
| D2. Memory Bank migration (from SQLite) | 3h |
| D3. Phase 2 integration (blind TDD from approved PRD) | 4h |

---

## 6. Key Design Decisions

### 6.1 Why the Orchestrator isn't just a loop in pipeline.py

An ADK Agent as orchestrator gets us:
- **IterationBudgetPlugin** — automatic halt on 50 turns or 200 tool calls
- **Resumable sessions** — if the server restarts mid-debate, the session survives
- **System prompt caching** — the stable tier of the prompt is cached, saving tokens
- **Tool result pruning** — old tool outputs are trimmed, keeping context window manageable
- **Standard ADK hooks** — after_agent_callback for auto_capture, before_tool_callback for guards

### 6.2 Why sub-agents remain separate LlmAgents (not inline prompt text)

The Advocate and Critic must be **different model instances** with different configurations:
- Advocate: Gemini 3.7 Flash, NO search tools, sees only the brief
- Critic: Gemini 3.1 Pro, HAS google_search, sees brief + Advocate's argument

If they were the same agent reasoning inline, it would be impossible to enforce the information asymmetry. The Critic would "know" it's supposed to find flaws, but it wouldn't have genuinely independent search access.

The harness's `delegate` pattern is exactly right for this: each sub-agent is a full `LlmAgent` with its own model, tools, and instruction, launched in an isolated session.

### 6.3 Why `load_memories()` runs at the START

Loading memories mid-run would bloat context. Loading at the start means:
- The orchestrator's system prompt already contains the key lessons
- The orchestrator has no excuse for "forgetting" to run the security audit
- If a lesson says "always verify market size with search", the orchestrator knows before calling advocate()

### 6.4 How human feedback becomes a lesson (the closed loop)

```
User clicks "Changes" + writes "You forgot security check"
  → dashboard.py calls:
      store.save_lesson("avoid", "security check was missing from PRD",
                        "human_feedback")
  → That's it. The review_fork will analyze this turn on the next pass,
    and the dream_review will consolidate it with other similar lessons.
  → Next run: load_memories() returns this lesson.
    Orchestrator MUST run audit() because the lesson says so.
```

This is the missing piece that makes VentureBot truly self-improving: human corrections become durable lessons, not one-off fixes.

---

## 7. Summary

| Dimension | Current (pipeline.py) | Target (orchestrator.py) |
|-----------|----------------------|--------------------------|
| Control flow | Hardcoded phase counter | Agent decides next action |
| Sub-agents | Called exactly once each | Called as needed, can loop |
| Clarify | Fires once, doesn't loop back | Pauses, resumes, can re-research |
| PRD revision | None (written once) | Self-review → redraft (can iterate) |
| Security audit | Runs once after PRD | Can run multiple times, mandatory before presenting |
| Memory reading | Not implemented | `load_memories()` injects past lessons |
| Human feedback | "Changes" → re-run pipeline from scratch | "Changes" → lesson stored → orchestrator applies in THIS run |
| Self-improvement | Forks write-only | Forks write → orchestrator reads → applies in next run |
| Iteration budget | `run_manager.check()` polling | `IterationBudgetPlugin` (harness pattern) |
| Session persistence | Custom checkpoint JSON | ADK resumable sessions |
| Guardrails | `input_guard.py` only | Full guard stack (exfil, repeated failure, budget) |

**The orchestrator is the engineering lead. The sub-agents are the specialists. The memory layer is the institutional knowledge. The human is the stakeholder who corrects course and whose feedback becomes permanent learning.**