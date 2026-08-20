# Phase 2 Feasibility & Reuse Strategy

**Date:** 2026-08-19  
**Status:** Analysis — two engines evaluated for Phase 2 blind TDD loop

## 1. Summary

Phase 2 (PO → TestWriter → Coder → QA_PO blind TDD) is out of scope for the
hackathon demo (PRD §11 — post-hackathon). However, there are two ready-made
engines that can dramatically shortcut a Phase 2 prototype instead of building
a custom code-generator loop from scratch.

## 2. Option A: Pi Agent (Codex-like coding agent)

**Package:** `@earendil-works/pi-coding-agent` v0.84.2 (installed on this VPS)  
**Author:** Mario Zechner  
**License:** MIT (full text in `package.json`)  
**Repository:** github.com/earendil-works/pi

### License Analysis

MIT — permits **commercial use, modification, private forks, distribution,
and sublicensing**. The only requirement is retaining the copyright notice
in derivative works. No copyleft, no reciprocal clause, no AGPL trap.

VentureBot is currently "private — hackathon submission." MIT code can be
incorporated without opening VentureBot's source. If VentureBot is later
open-sourced (e.g., after the hackathon), attribution to the Pi project
should appear in the README/credits — but that's a documentation concern,
not a license blocker.

### GCP Feasibility

Pi runs as a **plain Node.js ≥ 22.19.0 process** (we have v22.23.2, pi v0.84.2
verified working). Deployment options documented upstream:

| Method | How | Pi docs |
|--------|-----|---------|
| Plain Docker | `Dockerfile.pi` provided; deploy to Cloud Run | `docs/containerization.md` |
| Gondolin micro-VM | QEMU-based isolation, mounts host cwd | `examples/extensions/gondolin/` |
| Managed Sandbox | OpenShell gateway for policy-controlled isolation | `docs/containerization.md` |

**Google Vertex AI is natively supported** (`docs/providers.md`):
```bash
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT=your-project
export GOOGLE_CLOUD_LOCATION=us-central1
```
So Pi running on GCP can use Vertex/Gemini with a service account — no
API-key plumbing, unified billing with the rest of VentureBot.

### RPC Mode — the key enabler

Pi ships with `pi --mode rpc` (JSONL-over-stdio, `docs/rpc.md`), built for
"embedding in automated pipelines." This maps directly to the Phase 2 design:

| VentureBot Phase 2 Agent | Pi RPC process | Model flag |
|--------------------------|----------------|------------|
| TestWriter | `pi --mode rpc --model Y` | writes pytest from PRD |
| Coder | `pi --mode rpc --model X` | implements code from test failures |
| Reviewer (QA_PO) | `pi --mode rpc --model Z` | reviews code vs PRD + plan |

The Python backend would spawn 3 Pi subprocesses and orchestrate them over
stdin/stdout (JSON lines). The existing `sandbox.py` (unshare + setuid +
rlimits) wraps any file-write/pytest execution for safety.

**Caveats:**
- Pi is Node.js, not Python — orchestration is subprocess-based (RPC from
  FastAPI) or SDK-based (Node sidecar). Clean, but different shape than the
  in-process ADK agents of Phase 1.
- Pi in non-interactive mode has **no permission popups** (per docs) — the
  VentureBot sandbox already handles that via container/rlimit isolation.
- Pi is NOT Google ADK. Using it as the Phase 2 engine is fine for internal
  prototyping but the hackathon submission's ADK story lives in Phase 1 +
  PRD §7 "shadow mode" migration path. Pi stays internal; ADK is the public
  differentiator.
- **Estimated spike time:** ~1 day to wire 3 RPC subprocesses into the
  VentureBot FastAPI app.

## 3. Option B: ADK Samples — Long Horizon Harness (google/adk-samples)

**Path:** `/root/patchee-sandbox/adk-samples/core/python/long-horizon-harness/`  
**License:** Apache 2.0 (full text at `adk-samples/LICENSE`)  
**Disclaimer:** "Not an officially supported Google product — sample code for
demonstration only" (from `long-horizon-harness/README.md`)

### License Analysis

Apache 2.0 is **even more permissive than MIT** for this use case:
- Permits commercial use, modification, and distribution.
- Explicit patent grant (unlike MIT).
- Requires retention of copyright/attribution notices + inclusion of a copy
  of the license.
- Can be combined with MIT code freely (both are permissive, compatible).

VentureBot can reuse ADK sample code 1:1 or modified, as long as:
1. The Apache 2.0 license text is included in the distribution.
2. Modified files carry a notice stating they were changed.
3. The original copyright (`Copyright 2025 Google LLC`) is preserved.

These are all trivial to satisfy — a NOTICE or ACKNOWLEDGMENTS file in the
repo root + a line in the submission documentation.

### What VentureBot Already Reuses (from ADK Samples)

VentureBot's architecture is directly derived from these ADK samples:

| Pattern reused | Source file | VentureBot equivalent |
|----------------|-------------|-----------------------|
| Critic with `google_search` | `llm-auditor/llm_auditor/sub_agents/critic/agent.py` | `venturebot/agents/agents.py` — `critic_agent` |
| `after_model_callback` grounding | `llm-auditor/.../critic/agent.py` (`_render_reference`) | Cited in PRD §12; not yet wired in current code |
| SequentialAgent chain | `llm-auditor/llm_auditor/agent.py` | Custom orchestrator (`pipeline.py`) — SequentialAgent was deprecated |
| `google_search` tool pattern | `academic-research/academic_research/agent.py` | `venturebot/agents/agents.py` — `researcher_agent` |
| `response_schema` enforcement | `financial-advisor/financial_advisor/agent.py` | `venturebot/agents/agents.py` — `judge_agent`, `auditor_agent` |
| Structured output prompt format | `sdlc-task-planner/sdlc_task_planner/prompt.py` | `venturebot/agents/prompts.py` — JUDGE_PROMPT |
| PRD generation prompt style | `sdlc-technical-designer/sdlc_technical_designer/prompt.py` | `venturebot/agents/prompts.py` — PRD_WRITER_PROMPT |

And the self-improvement layer (M3) is modeled after the Long Horizon
harness's memory patterns:

| Pattern | Long Horizon source | VentureBot |
|---------|-------------------|------------|
| `auto_capture` | `horizon/memory/auto_capture.py` | `venturebot/memory/auto_capture.py` |
| `review_fork` | `horizon/memory/review_fork.py` | `venturebot/memory/review_fork.py` |
| `dream_review` | `horizon/memory/dream_review.py` | `venturebot/memory/dream_review.py` |
| Throttle | `horizon/memory/_throttle.py` | `venturebot/memory/_throttle.py` |

### What the Long Horizon Harness Offers for Phase 2

The Long Horizon harness is the closest ADK-native analogue to a full
Phase 2 TDD loop. It already has:

- **Sub-agents with delegation** (`horizon/subagents/delegate.py`) — the
  pattern for PO → TestWriter → Coder → QA_PO chaining.
- **Sandbox lifecycle** (`horizon/sandbox/`) — per-user sandbox warm between
  turns, reattached on next message. Maps to VentureBot's `sandbox.py`.
- **Guardrails** (`horizon/guardrails/`) — iteration budget, no-progress
  halt, permission guard, exfil guard. Maps to VentureBot's kill switch +
  output guard + input guard.
- **Self-improvement** (`horizon/memory/`) — the 3-fork pattern VentureBot
  already adopted. Long Horizon adds `skill_curator.py` (auto-generate
  reusable techniques → SKILL.md files), which is a natural M3 extension.
- **Cross-session memory** via Memory Bank (Vertex AI managed service) —
  VentureBot's SQLite store is the Stage 1 equivalent; the GCP deployment
  path (PRD §8.2) already documents how to swap to Memory Bank.
- **82K lines of Python, 200+ unit tests** — production-grade test coverage
  for the patterns you'd need to build for Phase 2.

### Recommendation for Phase 2 Shortcut

Use the Long Horizon harness **as the Phase 2 engine directly** — not just
as a reference. The harness is a full FastAPI app (`horizon/fast_api_app.py`)
with A2A support, sandboxing, and sub-agent delegation already working. The
play: mount VentureBot's Phase 1 debate agents as a Long Horizon sub-agent
(or, simpler: keep Phase 1 as VentureBot's own FastAPI, and spawn Long
Horizon as a sidecar for Phase 2 code generation, communicating over A2A).

This is more "ADK-native" than Pi (which is MIT but not ADK) and avoids
building a custom code-generator loop entirely. For the hackathon demo:

- **Phase 1 demo** → VentureBot's own FastAPI (already working).
- **Phase 2 prototype** → Long Horizon harness sidecar, adapted minimally.

## 4. Decision Matrix

| Criterion | Pi (RPC mode) | ADK Long Horizon Harness |
|-----------|--------------|--------------------------|
| License | MIT ✅ | Apache 2.0 ✅ |
| Runs on GCP | Docker/Cloud Run ✅ | Docker/Cloud Run + Agent Engine ✅ |
| ADK-native | ❌ (requires justification) | ✅ (built by Google engineers) |
| Build effort (prototype) | ~1 day (wire RPC subprocesses) | ~2-3 days (adapt harness to VentureBot PRDs) |
| Code generation quality | High (proven coding agent) | Unknown (harness is a framework, not a coder) |
| Sandbox security | Per existing VentureBot sandbox | Built-in sandbox lifecycle |
| Self-improvement | N/A (agent-specific) | Already modeled in M3 |
| Hackathon story | Weaker (not ADK) | Stronger (ADK from end to end) |
| Long-term maintainability | Depends on Pi's release cycle | Depends on adk-samples maintenance |

## 5. Conclusion

**For a hackathon Phase 2 mention/demo:** reference the ADK Long Horizon
harness as the planned engine. It's Apache 2.0, built by Google engineers,
and aligns perfectly with the "ADK from end to end" story. No need to build
anything — just cite it in the PRD §12 Reuse Map and the submission writeup.

**For a real, working Phase 2 prototype (post-hackathon):** either approach
works. Pi's RPC mode is the faster spike (~1 day vs ~3 days). The Long Horizon
harness is the more defensible long-term architecture. The PRD §7 "shadow mode"
migration path covers both: start with what works, measure, and promote ADK
when it's proven.

**Attribution required (either path):**
- Include `adk-samples/LICENSE` (Apache 2.0) in the VentureBot repo.
- Add a `NOTICE` or `ACKNOWLEDGMENTS.md` file recording which ADK sample
  files were adapted and from what repo/commit.
- In the README and hackathon submission, cite the reuse explicitly:
  *"VentureBot's agent architecture and self-improvement patterns are adapted
  from Google's ADK samples (Apache 2.0, github.com/google/adk-samples).
  Specific patterns borrowed: llm-auditor (debate chain + search grounding),
  long-horizon-harness (memory, sandbox, guardrails)."*