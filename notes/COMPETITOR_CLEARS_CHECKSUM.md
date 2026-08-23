# Competitor Scan: Clears.ai & Checksum.ai

**Date:** 2026-08-23 · **Type:** short recon for future brainstorming (not a deep dive)

---

## What they are

### Clears.ai — "Your Backlog, Delivered"
Autonomous execution layer for engineering teams: takes tickets/stories from backlog to reviewed PR.

| Pillar | What it does | How |
|---|---|---|
| **Triage & Intake** | Scores every incoming ticket on **agent-confidence 0–100%** (High/Med/Low bands) + risk (blast radius, data sensitivity) + complexity. Low confidence → flag for human or decomposition *before spending tokens*. Auto-identifies which repos a ticket touches. Scores come **with reasoning, not just numbers**. | Background analysis flows on incoming issues |
| **Context Layer** | Tiered memory: global / repo / session. After each story a model **distills raw execution data into living, deduplicated knowledge docs** (patterns, decisions, gotchas, API contracts). Semantic search: "how did we handle rate limiting?" → relevant past runs. "No cold starts." Editable, inspectable, ownable. | Post-run consolidation model + embeddings search |
| **Agentic Workflows** | Pipeline of specialized agents: Story Analysis → Approval Processing (decomposes spec into subtasks) → Implementation → PR Analysis → Validation (checks against definition of done). Each triggers automatically at lifecycle moment. **Always-on session per issue** that persists across runs. Live event streaming of every run. Human checkpoints at spec approval / subtask review / fix requests. | Orchestrated fleet + per-issue persistent sessions |
| **Work From Anywhere** | MCP server: drive everything from terminal/coding agent. | MCP |

### Checksum.ai — "Ship faster without trading off quality"
AI-generated, self-maintaining test suites ("continuous quality platform").

| Capability | Detail |
|---|---|
| **3 specialized agents** | E2E Agent (Playwright tests, auto-heals when app evolves); CI Agent (50–200 tests per PR, targeting changed code, executed before review); API Agent (thousands of endpoints in days) |
| **Always-on** | Lives in CI/CD, runs every commit; monitors production errors → "every bug becomes a test" |
| **Results-as-a-Service** | Pricing = # workflows maintained, not seats/runs. **Human engineer does final verification** before delivering tests. "100% ready-to-go, working tests" |
| **No lock-in** | Tests delivered as real Playwright code in YOUR repo, modifiable, portable |
| **Positioning vs copilots** | "On-demand vs always-on": Cursor/Claude write tests when asked; Checksum generates/executes/heals unprompted. Explicitly claims to remove the "fix AI-generated tests" loop |
| **Marketing** | Case-study-heavy with hard ROI numbers ($200K saved, 6 critical bugs/week caught, 40% manual-testing reduction, 10x QA impact) |

---

## Feature matrix vs VentureBot

| Concept | Clears | Checksum | VentureBot |
|---|---|---|---|
| Target user | Eng teams (backlog→PR) | Eng teams (QA automation) | Entrepreneurs/founders (idea→validated PRD) |
| Multi-agent orchestration | ✅ pipeline fleet | ✅ 3 agents | ✅ orchestrator + researcher/advocate/critic/creative/judge |
| Autonomous loop w/o prompting | ✅ background flows | ✅ core selling point | ✅ turn-based quality-gated loop |
| Confidence/scoring with reasoning | ✅ 0–100% agent-confidence + why | ❌ | ✅ judge scores 1–10 + rationale (n/10) |
| Human checkpoints where they belong | ✅ spec approve / review | ✅ human verification of tests | ✅ clarify pause, verdict gate, PRD approval |
| Durable pause/resume, recoverable runs | ✅ "always recoverable" | n/a | ✅ (just built: durable clarify pause) |
| Tiered memory + self-distilling knowledge | ✅ flagship feature | ❌ | ⚠️ lessons store + dream-review — less mature, no semantic tiers |
| Live observability of runs | ✅ event streaming per run | ❌ (tests just appear) | ✅ debate feed + participant chips + economics |
| Web/market research | ❌ | ❌ | ✅ researcher w/ sources, competitor gaps, TAM/SAM/SOM |
| Adversarial critique of an idea | ❌ (only feasibility triage) | ❌ | ✅ advocate/critic/judge debate |
| Output artifact | merged PRs | Playwright test code | validated verdict + PRD (+md/pdf export) |

## Is our niche still OK?

**Yes — direct overlap is low.** Both products assume the *what* is already decided (a ticket exists, a feature is scoped) and optimize the build/verify path. VentureBot operates **upstream**: deciding what's worth building at all, with market evidence, adversarial debate, and a kill/pivot/proceed verdict. Neither does external market research, competitor gap analysis, or idea-level verdicts.

**Adjacent-risk to watch:** Clears' *Story Analysis + triage confidence scoring* is structurally similar to our "feasibility verdict" shape — if they ever point it at greenfield ideas ("should this product exist?"), they're one pivot away. Same for Checksum moving earlier into planning.

## What to steal (brainstorm fodder)

1. **Confidence-banded gating (Clears)** — present judge verdict as PROCEED/PARK/PRUNE **plus** an explicit "agent-confidence %: how much do we trust this verdict given research quality?" — flags thin-research verdicts instead of confidently wrong ones.
2. **Self-distilling memory (Clears)** — our lessons store should *consolidate*: after each debate, distill transcript → deduplicated living knowledge docs (per topic), not append-only logs. Semantic search over past debates ("ideas like X were rejected because Y").
3. **Human checkpoints framing (Clears)** — they market "checkpoints exactly where they belong" as a *feature*. Our verdict gate + PRD approval + durable clarify pause is the same pattern — name it and sell it.
4. **ROI-number marketing (Checksum)** — their landing page is all quantified outcomes ($ saved, bugs caught). We should collect equivalent metrics from our own runs (hours of research compressed, competitors surfaced, verdict accuracy over time).
5. **Human verification tier (Checksum)** — "an expert verifies before you see results" builds trust in AI output. A "human-reviewed report" upsell tier is plausible for us later.
6. **Own-the-artifact (Checksum)** — tests live in your repo. Our PRD export could go further: emit machine-readable artifacts (issue backlog, acceptance criteria as YAML/Gherkin) so the PRD plugs directly into tools like… Clears or Checksum. **Integration opportunity: VentureBot as the front door of the SDLC** (validate → hand off to their execution).
7. **MCP interface (Clears)** — expose VentureBot over MCP so founders can run validations from their terminal/editor.

## Open questions for brainstorming

- Should VentureBot integrate *downstream* (feed Clears/Checksum pipelines) instead of competing?
- Is there a wedge where founders AND eng teams overlap (internal innovation teams, hackathon triage, RFP evaluation)?
- Do we add a "verdict confidence" layer without slowing the debate?
