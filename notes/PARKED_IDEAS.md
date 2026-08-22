# Parked Ideas

**Date:** 2026-08-21
**Status:** Ideas parked for future work, not blocking hackathon

---

## 1. MCP Config (S8) — PARKED

**Original requirement:** M0.5 Safety Baseline includes S8 MCP (Model Context Protocol) config
**Decision:** Not needed for hackathon demo. Drop from M0.5 completion criteria.
**Future:** May be needed for advanced tool integration, but not critical for core debate pipeline.

---

## 2. Long-Horizon-Harness Migration — PARKED (HIGH PRIORITY)

**Context:** Google ADK provides a production-grade long-horizon-harness pattern with:
- Proper session persistence (ResumabilityConfig)
- Context caching (ContextCacheConfig)
- Built-in iteration budgeting (IterationBudgetPlugin)
- Robust error recovery
- Artifact management
- Callback-based cross-cutting concerns

**Current state:** We have a custom while loop in `run_orchestrator()` that works but is fragile:
- No session persistence across restarts
- Manual stall detection
- No context caching
- Basic guardrails

**Decision:** Park for post-hackathon. **HIGH PRIORITY** for next iteration.
**Why park now:** Migration would take significant refactoring. Current implementation is sufficient for hackathon demo.
**Why high priority:** For production viability, we need the harness's robustness.

**Migration scope (future):**
- Restructure orchestrator to use harness's callback/plugin architecture
- Migrate session management to ADK's ResumabilityConfig
- Implement context caching with ContextCacheConfig
- Move quality gates into harness framework
- Test extensively

---

## 3. Multi-User GCP Deployment — PARKED (NOT READY)

**Context:** Deploying VentureBot to public GCP with multi-user support.

**Privacy/GDPR concerns:**
- User ideas must be isolated and safe
- GDPR compliance requires proper data handling
- Current architecture doesn't have user-level data isolation

**Proposed architecture (too foggy for hackathon):**
1. **Queue system**: Only persist what's currently running in the queue
2. **Local storage**: Users store ideas/history on their local machine
3. **Export/Import**: Allow users to export their data and import to another platform
4. **Bring Your Own Key (BYOK)**: Users provide their own Gemini API keys to minimize our costs
5. **Cost control + rate limiting**: Still needed to prevent abuse

**Decision:** Not ready for hackathon. Too many architectural unknowns.
**Future:** If VentureBot continues beyond hackathon, this needs thorough design before implementation.

**Why park:** Privacy is critical. If we can't keep user ideas separated and GDPR-compliant, we fail. Better to park than deploy something unsafe.

---

## Next Steps (Post-Hackathon)

1. **Long-horizon-harness migration** (HIGH PRIORITY)
2. **Multi-user architecture design** (needs thorough planning)
3. **GDPR compliance review** (legal review needed)
4. **BYOK implementation** (if multi-user goes forward)
