# Prototype Phase — Login Disabled (Temporary)

**Date:** 2026-08-20
**Decision:** Remove login for now. Reintroduce it during the multi-user feature.

---

## Rationale

VentureBot is currently a **single-user prototype**. The goal of the next phase is
to run it, gather real usage experience, and tune the debate/pipeline UX **before**
adding multi-user complexity.

Allowing multiple users forces: queue system, per-user isolation, rate limiting,
budget caps, BYOK key handling, GDPR/compliance, and a full multi-tenant refactor
(see `PUBLIC_DEPLOYMENT_DESIGN.md`). None of that should be built before we know
the single-user product is actually usable.

Google SSO is also currently broken on `venturebot.taskmind-ai.com` (OAuth origin
not registered) — but that is a symptom, not the driver. The driver is: **login has
no purpose until there are multiple users.**

## What changed

- `VENTUREBOT_NO_AUTH=1` (default ON for the prototype phase)
- `auth.get_current_user()` returns a synthetic "local" user when NO_AUTH is set,
  so every protected route keeps its guard but passes through.
- Frontend: login gate removed; the app shows immediately with a "local" badge.
- `src/auth.py` and all `/api/auth/*` routes are **kept intact** (still tested)
  for the multi-user phase — they are bypassed, not deleted.

## Reintroducing login (multi-user phase)

1. Set `VENTUREBOT_NO_AUTH=0` (or remove it).
2. Add `https://<domain>` to the Google OAuth client's Authorized JavaScript origins.
3. Remove the allowlist (`ALLOWED_EMAILS`) and add `user_id` scoping everywhere —
   see `PUBLIC_DEPLOYMENT_DESIGN.md` §4–§6.

## Open item (for review by another model)

The full multi-user + compliance design needs a **brainstorming and review pass by a
different model** before implementation. Noted as a pre-implementation gate. The
specific concerns to pressure-test:

1. Queue model: does freeing a worker slot at HITL gates actually improve throughput,
   or just add state-management complexity?
2. BYOK: is passing a user's API key to our server (even transiently, memory-only)
   acceptable, or does it defeat the "bring your own key" privacy promise?
3. Per-run state refactor: is `RunContext` the right abstraction, or is a separate
   worker process + message queue simpler to reason about?
4. Rate limiting: in-process token bucket vs. external (Redis) — when does in-process
   stop being enough?
5. Legal & Compliance agent: does it belong *inside* the debate pipeline, or should it
   be a pre-flight gate before the pipeline runs?
