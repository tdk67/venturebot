# VentureBot — Implementation TODO & Requirements

**Date:** 2026-08-20
**Status:** Prioritized backlog — ready for implementation

---

## P0 — Fix Foundation (do first)

### P0.1 Systematic Web Research
The Researcher currently makes its own ad-hoc search decisions. Replace with a mandatory 10-category search checklist:
- Competitors + pricing
- Reddit community discussions
- Hacker News threads
- ProductHunt launches
- GitHub open-source projects
- G2/Capterra reviews + complaints
- Market size (TAM/SAM/SOM)
- Google Trends / demand direction
- Funding/investment data (Crunchbase)
- Technical stack (APIs, SDKs, libraries)

Each search category must produce URLs that flow into the structured output schema (`PriorArt`, `MarketSignal`, `TechnicalLandscape` with `url` fields).

**Files:** `src/agents/prompts.py` — rewrite RESEARCHER_PROMPT

### P0.2 Per-Claim Source Linking
Every factual claim in the PRD and verdict must link to its source URL. The data exists (google_search returns URLs) but isn't structured. Add to the PRD Writer and Judge prompts: "Cite every factual claim with its source URL inline."

**Files:** `src/agents/prompts.py` — rewrite PRD_WRITER_PROMPT, JUDGE_PROMPT

### P0.3 PRD Completeness Scanner
Deterministic gate (like `artifact_scanner.py`) that checks PRD for:
- All required sections present (Overview, FRs, NFRs, Architecture, Acceptance Criteria, Milestones, Risks)
- Every FR has at least one acceptance criterion
- Security/auth/data-handling section present
- No unsourced factual claims (heuristic: numbers/dates/product names without URLs)
- Run BEFORE the orchestrator can present to human

**Files:** new `src/prd_scanner.py`, wire into `src/agents/orchestrator.py` quality gate

### P0.4 Self-Improvement Proof (Eval Harness)
Build a script that:
1. Runs the same idea twice — once with `load_memories()` empty, once with lessons from a human-corrected previous run
2. Measures: human corrections needed, PRD completeness score, time to approval, stall count
3. If second run shows measurable improvement → self-improvement works. If not → fix it.

Also add a **degradation guard**: before loading a lesson from the memory store, check if it contradicts an existing active lesson. If it does, flag for dream_review resolution instead of blindly loading both.

**Files:** new `tests/test_self_improvement.py` (eval harness), `src/memory/sqlite_store.py` (contradiction detection)

### P0.5 Idea Resume With Full Context
`/api/ideas/{id}/resume` currently just re-submits the idea title. It must load and pass to the orchestrator:
- Previous research brief
- Previous debate transcript
- Previous verdict + scores
- Previous PRD (if any)
- All previous run history for this idea

The orchestrator should receive this as pre-existing context and continue from where it left off, not start fresh.

**Files:** `src/dashboard.py` (API), `src/agents/orchestrator.py` (context injection)

---

## P1 — Polish & Differentiators (do second)

### P1.1 Idea History — Full Debate Evolution Timeline
**Requirement from user:** "The idea history should contain the whole history — what debates were running, how the idea has evolved. If I open the idea on the UI I should be able to see results of all previous debates."

The idea detail view must show:
- Timeline of ALL runs for this idea (dates, verdicts, scores)
- How scores changed across runs (novelty/feasibility/market_fit trend chart)
- Full debate transcript for each run (collapsible)
- PRD versions and what changed between them (diff)
- Human interventions and what feedback was given
- Current status + recommendation ("this idea improved from 5.3 to 7.1 after the technical POC — time to build")

**Data model change:** `idea_tree` needs a `runs` child table:
```sql
CREATE TABLE idea_runs (
    id TEXT PRIMARY KEY,
    idea_id TEXT NOT NULL,
    run_number INTEGER NOT NULL,
    status TEXT,           -- running, completed, cancelled
    verdict TEXT,          -- PROCEED, PARK, PRUNE
    scores TEXT,           -- JSON {novelty, feasibility, market_fit}
    research_brief TEXT,
    debate_transcript TEXT, -- JSON array of events
    prd_text TEXT,
    prd_version INTEGER DEFAULT 1,
    security_audit TEXT,
    human_feedback TEXT,   -- what the human said
    turns_used INTEGER,
    created_at REAL,
    FOREIGN KEY (idea_id) REFERENCES idea_tree(id)
);
```

**Files:** `src/memory/sqlite_store.py` (schema migration + CRUD), `src/dashboard.py` (API + UI), `templates/index.html` (frontend)

### P1.2 Second Brain — Dust Off Old Ideas
**Requirement from user:** "Our solution should be like a second brain that will help the users go back in time and check what happened with an idea a year ago — maybe time to blow the dust off and continue working on it. Or user wants to check an older idea and turn it with a new argument."

Features:
- "Revive" button on PARKED/PRUNED ideas that reloads full context and asks: "What changed? New evidence? New market condition? New technology?"
- Periodic `dream_review` check: "Has anything changed in the market that makes this PARKED idea viable again?" (e.g., a competitor shut down, a new API launched, a regulation changed)
- "Time capsule" view — see the idea as it was on a specific date, with that era's market conditions
- "What if I pitched this differently?" — re-run the debate with a modified angle/story while preserving the original

**Files:** `src/memory/dream_review.py` (revival checks), `src/dashboard.py` (UI), `src/agents/orchestrator.py` (revival mode)

### P1.3 Iterative Feasibility POC Generation
**Requirement from user:** "The decision what part of the idea needs code generation should be discussed with the user and it is an iterative process: take one part — implement, check weakness, refine PRD, take another part — implement — evaluate, refine PRD."

The loop:
1. After PRD + verdict, orchestrator identifies the riskiest technical claim (from Judge's feasibility rationale)
2. `clarify("The riskiest part is X. Should I build a feasibility POC for this? This costs ~$0.50 in API calls.")`
3. Human approves → orchestrator spawns Phase 2 lite: a focused coding agent that builds JUST the risky component and runs it
4. Results feed back: "The Stripe Connect integration works for marketplace payouts. Latency is 200ms. Feasibility score should be 8, not 6."
5. Judge re-runs with the new evidence → updated scores
6. PRD updated with POC findings
7. Next riskiest part identified → loop back to step 2

This is NOT "build the whole MVP." It's "prove the scariest assumption, then re-score." Each POC cycle improves the Feasibility score with evidence, not guesses.

**Files:** new `src/poc_generator.py` (focused coding agent), `src/agents/orchestrator.py` (POC loop)

### P1.4 "Quick Scan" Mode
Single-agent research + verdict in ~60 seconds, no full debate. Competitors (Preuve, PainMap) are instant — we need a fast path. Full debate becomes "Deep Dive."

**Design:** Quick scan runs Researcher + Judge only (skip Advocate/Critic/Creative/Audit). Returns viability score + competitor map + top 3 risks. Deep dive runs the full orchestrator loop.

**Files:** `src/agents/orchestrator.py` (mode switch)

### P1.5 Summary Report (1-Pager)
Generate a Preuve-style one-page summary from the full PRD:
- Viability score (0-100) with breakdown
- Top 3 competitors with pricing
- Key market signals
- Top 3 risks
- "Should you build this?" verdict with rationale
- Link to full PRD for technical depth

**Files:** new prompt or agent for report generation, wire into orchestrator output

---

## P2 — Advanced (do third)

### P2.1 Idea Comparison
Side-by-side comparison of multiple ideas: scores, risks, market sizes, PRD summaries. "Which of my 3 ideas should I build first?"

**Files:** `src/dashboard.py` (UI), `src/memory/sqlite_store.py` (comparison query)

### P2.2 Degradation Guard for Self-Improvement
Before accepting a new lesson from `review_fork` or `dream_review`:
- Check if it contradicts any active lesson
- If contradiction detected → flag both, run LLM-assisted resolution
- Track lesson "success rate" — does applying this lesson correlate with better outcomes?
- Auto-retire lessons that haven't been reinforced in N runs

**Files:** `src/memory/sqlite_store.py`, `src/memory/dream_review.py`

### P2.3 Google Trends Integration
Add a `google_trends` tool that returns interest-over-time data for search terms. Feed into Researcher and surface in the verdict as "demand direction: rising/falling/stable."

### P2.4 Shareable PDF Export
Generate a nicely formatted PDF from the PRD + summary report. For sharing with co-founders, investors, advisors.

### P2.5 Competitor Monitoring
For PARKED ideas: periodically re-check if competitors have changed (new funding, shutdown, pivot). Alert the user if the landscape shifted. "Competitor X shut down — your PARKED idea from March might be viable now."

---

## Implementation Order

```
Week 1: P0.1 (research breadth) + P0.3 (PRD scanner) + P0.5 (idea resume)
Week 2: P0.2 (source linking) + P0.4 (self-improvement proof)
Week 3: P1.1 (idea history timeline) + P1.4 (quick scan)
Week 4: P1.3 (feasibility POC loop) + P1.5 (summary report)
Week 5: P1.2 (second brain revival) + P2.1 (idea comparison)
Later: P2.2-P2.5
```

---

## Key Design Principle From User

> "Our solution should be like a second brain that will help the users go back in time and check what happened with an idea a year ago — maybe time to blow the dust off and continue working on it."

This means:
- **Never delete anything.** PRUNE doesn't remove data — it archives with a reason.
- **Every run is a snapshot.** You can rewind to any point in the idea's evolution.
- **The system should proactively suggest revivals** when market conditions change.
- **Context is preserved across time gaps.** If you come back after 6 months, VentureBot should load the full history and say "Here's where we left off. Here's what's changed since then."