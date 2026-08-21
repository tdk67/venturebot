# VentureBot — Claims vs. Reality Audit + What To Steal From Competitors

**Date:** 2026-08-20
**Method:** Line-by-line code audit of every major feature claim

---

## Part 1: Claims Audit — What's Real, What's Not

### ✅ DEEP RESEARCH — CLAIM: "Finds prior art, market signals, competitor analysis"
**Reality: Shallow single-source search.**

The Researcher agent's prompt (prompts.py) tells it to call `google_search` for:
- GitHub repos ✅
- Prior art / existing products ✅
- Market signals (forum discussions, trends, funding) ✅
- Technical feasibility (APIs, SDKs) ✅

**What's MISSING vs. Preuve AI (60+ sources):**

| Source | Preuve AI | VentureBot |
|--------|-----------|------------|
| Google Search | ✅ | ✅ |
| Reddit communities | ✅ | ❌ Not in prompt |
| Hacker News | ✅ | ❌ Not in prompt |
| LinkedIn | ✅ | ❌ |
| Product Hunt | ✅ | ❌ |
| Google Trends | ✅ | ❌ |
| Regulatory filings | ✅ | ❌ |
| Funding databases (Crunchbase) | ✅ | ❌ |
| Market research firms | ✅ | ❌ |
| G2/Capterra reviews | ✅ (PainMap does this) | ❌ |
| App Store reviews | ✅ (PainMap) | ❌ |
| Patent databases | ❌ | ❌ |

**The gap:** Our Researcher runs ONE google_search call series against a vague prompt. Preuve runs 10 agents across 60+ sources. PainMap mines specific communities. We ask Gemini "search the web" and hope it picks good queries and synthesizes well. It's a single agent's best effort, not a systematic sweep.

**Fix:** Rewrite the Researcher prompt to explicitly target SPECIFIC sources:
```
YOU MUST search across ALL of these categories (minimum one search each):
1. google_search: "[idea] competitor products pricing 2026"
2. google_search: "[idea] site:reddit.com OR site:news.ycombinator.com"
3. google_search: "[idea] site:producthunt.com"
4. google_search: "[idea] open source site:github.com"
5. google_search: "[idea] market size trends growth"
6. google_search: "[idea] customer reviews complaints site:g2.com OR site:capterra.com"
7. google_search: "[idea] funding investment site:crunchbase.com"
```
This forces breadth. The Researcher's output schema already has `PriorArt`, `MarketSignal`, `TechnicalLandscape` with URL fields — the structure is there, the search coverage is not.

---

### ⚠️ SELF-IMPROVEMENT — CLAIM: "Gets better cycle over cycle"
**Reality: Infrastructure exists, zero proof it works.**

What we have:
- `auto_capture.py` — saves turn transcripts to SQLite ✅ (works, tested)
- `review_fork.py` — spawns a mini-agent to analyze one turn and propose lessons ✅ (works, tested)
- `dream_review.py` — nightly consolidation + idea tree pruning ✅ (works, tested)
- `sqlite_store.py` — 5 tables with CRUD ✅ (works, tested)
- `orchestrator.py` `load_memories()` — reads lessons at start of run ✅ (new, untested end-to-end)

**What we DON'T have:**
1. **No metrics to prove improvement.** We've never run the same idea twice and measured: did the second run produce fewer human corrections? Shorter time to approval? Higher quality scores? There's no benchmark, no eval harness for the self-improvement loop.

2. **No degradation guard.** If the review_fork proposes a BAD lesson (e.g., "always skip market research — it's noisy"), that lesson gets fed back into the orchestrator on the next run with NO validation. The self-improvement loop has no anti-regression check.

3. **No contradiction detection beyond dream_review's prompt.** The dream_review consolidation prompt ASKS the LLM to resolve contradictions, but there's no automated check. A lesson like "always do X" and "never do X" could coexist in the store and be fed to the orchestrator.

4. **load_memories() is new and untested.** It was just added in the orchestrator. We don't know if the orchestrator actually APPLIES the lessons or just acknowledges them and moves on.

**Proof needed:**
- Run 5 identical ideas with `load_memories()` disabled → record quality metrics (human corrections needed, time to approval, PRD completeness)
- Enable lessons from a human-corrected run → run the SAME 5 ideas again → compare metrics
- If the second pass shows MEASURABLE improvement (fewer corrections, faster), we have proof
- If it shows NO improvement or degradation, we have a problem to fix

---

### ⚠️ IDEA TREE — CLAIM: "Keep, park, prune, restart, refine ideas"
**Reality: Storage exists, restart doesn't.**

What works:
- SQLite `idea_tree` table stores title, scores, status, research_brief, debate_transcript, prd_text ✅
- Deterministic pruning rules (score <5 + 24h → prune, 7d inactive → park, 30d parked → prune) ✅
- Dashboard shows ideas with filtering, facets, CSV export ✅
- `/api/ideas/{id}/resume` exists ✅

**What's broken:**
- `/api/ideas/{id}/resume` just calls `_inbox.add_idea(idea["title"])` — it loads the TITLE as a new idea. It does NOT restore the research brief, debate transcript, or PRD as context. The orchestrator starts FROM SCRATCH with just the title text.
- There's no "refine this idea" path — no way to say "the verdict was PARK, but I found new evidence, re-debate with this additional context"
- There's no "compare ideas" feature — no side-by-side scoring

**What competitors do better:**
- Preuve AI: "3 pivot iterations included per paid report" — you can refine without starting over
- IdeaProof: "one workspace for the whole early-stage workflow" — ideas persist as projects
- None of them have a TREE structure with pruning — this IS genuinely unique if we fix resume

**Fix:** `/api/ideas/{id}/resume` should load the full stored context (research_brief, debate_transcript, scores) and pass it to the orchestrator as PRE-EXISTING CONTEXT, not just re-run the title. The orchestrator should see: "The previous debate reached verdict X with scores Y. The human wants to revisit this. Here's the previous research brief and debate transcript. Start from the research phase with this context."

---

### ✅ ADVERSARIAL DEBATE — CLAIM: "Different models, blind vs. search"
**Reality: Actually works as designed. No claims issue.**

The Advocate (gemini-3.7-flash, no tools) and Critic (gemini-3.1-pro, google_search) genuinely have different models and different information access. The orchestrator preserves this. This is real.

---

### ⚠️ PRD SELF-REVIEW — CLAIM: "Orchestrator reads own PRD, finds gaps, re-drafts"
**Reality: Prompt instructs it, but no enforcement.**

The orchestrator's system prompt says to read PRD.md, check for gaps, and call write_prd() with revision instructions. But:
- The orchestrator might SKIP this step if it "decides" the PRD is fine
- There's no automated completeness check — no schema validation, no required-section scanner
- The quality gate only checks "did the PRD text change?" (stall detection) — not "are all sections present?"

**Fix:** Add a deterministic PRD completeness scanner (similar to `artifact_scanner.py`) that checks for required sections, acceptance criteria coverage, and source citations BEFORE the orchestrator can present. Make it a hard gate, not a prompt suggestion.

---

### 🔴 PHASE 2 CODING — CLAIM: "PRD → MVP via blind TDD"
**Reality: Separate pipeline, not integrated, no anti-degradation gate.**

Phase 2 (`blind_tdd/`) is a separate codebase that:
- Takes a PRD → PO writes tasks → TestWriter writes tests → Coder implements → QA_PO reviews
- Runs on OpenRouter (not ADK)
- Is NOT wired to the orchestrator's output
- Has NO anti-degradation gate (the shadow mode from idea-02.md)
- Test suite passes but the pipeline itself is fragile

**What you said is right:** Instead of trying to build the WHOLE MVP, we should build a **feasibility POC generator** — pick the riskiest technical assumption from the PRD and prototype JUST that part:
- "Can we actually call this API and get useful data?"
- "Does this ML model work on the target hardware?"
- "Is the latency acceptable for this real-time feature?"

This is MORE valuable than a full MVP because it directly addresses the Feasibility score from the verdict. If the Judge scored Feasibility 6/10 because "uncertain if the Stripe Connect integration handles marketplace payouts correctly" → the POC builds exactly that integration and reports results. This turns a guess into evidence.

---

## Part 2: Features To Steal From Each Competitor

### From Preuve AI (#1):
1. **Per-claim source linking** — Every factual claim in the output links to its source URL. We have the data (search results return URLs), we need to structure it.
2. **60+ source categories** — Reddit, HN, LinkedIn, ProductHunt, Google Trends, regulatory filings, funding DBs, market research firms. Our Researcher should explicitly search these.
3. **0-100 viability score** — Their single-number score with methodology transparency is cleaner than our N/F/M three-axis. Keep the axes internally, but present a unified score + the breakdown.
4. **3 pivot iterations included** — They let you refine the idea without paying again. Our idea tree should support this natively.
5. **Free tier that's actually useful** — "Free scan gives viability score, market overview, competitor previews, blockers." We need this.

### From DimeADozen (#2):
1. **Polished 40+ page document** — Their output is shareable. Our PRD is engineer-focused. We need a "Summary Report" mode.
2. **Sourced data from public filings** — They pull from SEC/EDGAR/etc. for hard numbers.

### From PainMap (#4):
1. **Community pain mining** — Reddit, X, G2, Capterra, Trustpilot, vendor blogs for customer complaints. This is a specialized search we don't do.
2. **Pain score ranking** — Opportunity score per pain point. We should add this to the Researcher's output.
3. **Willingness-to-pay quotes** — They surface actual user quotes about pricing. Powerful for market fit evidence.

### From WorthBuild (#5):
1. **Lead discovery** — Alongside validation, they surface potential early users from Reddit/HN/Twitter. Unique value-add.
2. **$5/report pricing** — Sets the floor. We need to know our price point relative to this.

### From Trend Seeker (#6):
1. **Interest-over-time charts** — Google Trends integration to show whether demand is rising or fading. We could add this as a structured data point.

### From IdeaProof (#3):
1. **All-in-one workspace** — Validation + business plan + branding + marketing assets. We don't need all of this, but the idea of a "project" that persists beyond a single run is strong.

---

## Part 3: The Three Hard Problems To Solve First

### Problem 1: Prove Self-Improvement Works (or Kill It)

We need an eval harness:
```
Run 1: idea "X" → count human corrections needed → score PRD quality (1-10)
Save human corrections as lessons via /api/feedback
Run 2: same idea "X" → same metrics
Run 3: different idea "Y" → check if lessons from X transfer
```

If Run 2 has FEWER corrections than Run 1, the loop works.
If Run 2 has MORE or the SAME, the loop doesn't work yet.
If Run 3 benefits from Run 1's lessons, we have cross-idea improvement (the holy grail).

This requires ~3 runs × 2 ideas = 6 runs, each 5-10 minutes, cost ~$3-6 in API. Do this BEFORE claiming self-improvement works.

### Problem 2: Make Web Research Systematic, Not Haphazard

The Researcher currently makes its own decisions about what to search. This is too narrow.

**Fix:** Hard-code a search checklist in the Researcher prompt:
```python
REQUIRED_SEARCHES = [
    ("competitors", "[idea] vs [idea] competitors comparison pricing"),
    ("reddit", "[idea] site:reddit.com"),
    ("hackernews", "[idea] site:news.ycombinator.com"),
    ("producthunt", "[idea] site:producthunt.com"),
    ("github", "[idea] site:github.com"),
    ("complaints", "[idea] complaints problems site:g2.com OR site:capterra.com"),
    ("market_size", "[idea] market size TAM SAM SOM"),
    ("trends", "[idea] google trends interest growth"),
    ("funding", "[idea] funding investment series"),
    ("technical", "[idea] API SDK integration technical stack"),
]
```

This guarantees 10+ searches across diverse sources. The Researcher's output schema already supports structured results — we just need to ensure breadth.

### Problem 3: Fix the Idea Lifecycle (Store → Resume → Refine → Compare)

The idea tree is a DIFFERENTIATOR if it works. Right now it's a write-only log.

**What "works" means:**
1. **Resume with full context** — Load previous research brief, debate transcript, PRD, and scores as context for a new orchestration run
2. **Refine with new evidence** — "I found this article about my competitor's pricing change. Re-run the debate with this new context."
3. **Compare ideas** — Side-by-side: scores, risks, market sizes. "Which of my 3 ideas should I build first?"
4. **Track evolution** — See how an idea's scores changed across multiple refinement runs. Did the Feasibility score go up after the technical POC?

None of these exist today. All are implementable with the existing SQLite schema.

---

## Part 4: Revised Feature Priority

| Priority | Feature | Current Status | Effort |
|----------|---------|---------------|--------|
| **P0** | Systematic web research (10+ search categories) | Shallow | 2h (prompt rewrite) |
| **P0** | Idea resume with full context | Broken | 3h |
| **P0** | Self-improvement proof (eval harness + metrics) | None | 4h (run + measure) |
| **P1** | Per-claim source linking in output | Partial (data exists) | 3h (prompt + schema) |
| **P1** | PRD completeness scanner (deterministic gate) | None | 2h |
| **P1** | "Quick scan" mode (single-agent, ~60s) | None | 3h |
| **P1** | Summary report (1-pager for sharing) | None | 2h |
| **P2** | Idea comparison (side-by-side) | None | 3h |
| **P2** | Feasibility POC generator (Phase 2 lite) | Separate pipeline | 6h |
| **P2** | Degradation guard for self-improvement | None | 4h |
| **P3** | Google Trends integration | None | 2h |
| **P3** | Shareable PDF export | None | 3h |

---

*This is the honest inventory. Most of the architecture is sound. Most of the implementation is shallow. The path forward is: deepen the research, prove the self-improvement, fix the idea lifecycle, THEN add the fancy features.*