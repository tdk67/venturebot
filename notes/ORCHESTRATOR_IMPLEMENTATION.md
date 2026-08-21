# Orchestrator Implementation Summary

## Files Changed

### New: `src/agents/orchestrator.py` (906 lines)
The autonomous agentic loop that replaces the sequential pipeline. Key components:

1. **OrchestratorResult** — dataclass tracking the full run state (research, debate, verdict, PRD, security audit, events, clarification state)

2. **OrchestratorTools** — function tools wrapping each sub-agent:
   - `load_memories()` — reads past lessons from SQLite BEFORE any work
   - `research(idea)` — runs the Researcher sub-agent (flash + search)
   - `advocate()` — runs the Advocate sub-agent (flash, blind)
   - `critic()` — runs the Critic sub-agent (pro + search)
   - `creative()` — runs the Creative sub-agent (flash, hot temp)
   - `judge()` — runs the Judge sub-agent (pro)
   - `write_prd(instructions?)` — runs PRD Writer; optional revision instructions
   - `audit()` — runs Auditor + deterministic scanner
   - `read_file(path)` / `write_file(path, content)` — workspace file I/O
   - `save_artifact(path)` — mark artifact for download
   - `clarify(question)` — REAL HITL using asyncio.Event to pause/resume

3. **Quality Gate** (`_check_quality_gate`):
   - Stops when human approved/rejected
   - Stops when audit passes (PRD ready for approval)
   - Stops when PRD unchanged for 3 turns (stall detection)
   - Stops at max turns (10, configurable) with PRD+verdict
   - Stops at max turns even without PRD (budget exhausted)

4. **Drive Loop** (`run_orchestrator`):
   - Builds the orchestrator agent with all function tools
   - Runs turn by turn, checking quality gate before + after each turn
   - Each turn gives the orchestrator a state summary + "what to do next"
   - Auto-runs security audit before presenting if not done yet
   - Final status: needs_approval, needs_verdict, done, stopped, failed

### Modified: `src/config.py`
Added orchestrator loop budget settings:
- `ORCHESTRATOR_MAX_TURNS` (default 10)
- `ORCHESTRATOR_MAX_TOOL_CALLS` (default 50)
- `ORCHESTRATOR_STALL_TURNS` (default 3)
- `MODEL_ORCHESTRATOR` (default gemini-3.1-pro-preview)

### Modified: `src/dashboard.py`
- Replaced `run_debate` with `run_orchestrator` in `/api/run-phase1`
- Replaced `resume_debate` with orchestrator-based resume in `/api/resume`
- Added `/api/clarify/answer` for answering pending clarify() calls
- Added `/api/feedback` — human → lesson pipeline
- `/api/checkpoints` and `/api/paused` now read from orchestrator _RUNS
- Event broadcasting now includes `turns_used` in run_finished events

### Modified: `.env.example`
Added orchestrator settings.

## Key Design Decisions

### Sub-agents stay as separate LlmAgents
The Advocate/Critic asymmetry (blind vs. search) requires different model instances with different tool configurations. The orchestrator calls them as function tools, each running in its own ADK Runner context.

### Clarify is real HITL
Uses `asyncio.Event` to actually pause the orchestrator loop. The dashboard's `/api/clarify/answer` endpoint calls `answer_clarify()` which sets the event, unblocking the orchestrator.

### Self-improvement: load_memories() closes the loop
The memory layer (auto_capture, review_fork, dream_review) already writes lessons. `load_memories()` is the READ path that makes them actually apply to future runs.

### Human feedback → lesson
`/api/feedback` captures human corrections as `agent_lessons` rows. On next run, `load_memories()` returns them and the orchestrator MUST apply them.

### Quality gate prevents infinite looping
Multiple stopping conditions ensure the orchestrator doesn't loop forever:
- Clean audit → present (success)
- No progress for 3 turns → present what we have
- Max turns reached → present what we have

## Remaining for future PRs
- The orchestrator's turn prompt doesn't yet include the past memories text (they're loaded via tool call on turn 1)
- Phase 2 integration (blind TDD from approved PRD)
- Deploy to GCP Agent Engine