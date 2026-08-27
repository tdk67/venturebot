# T2 — Orchestrator hardening (evidence)

Task: REWRITE_PLAN.md Part C, T2.
Verification points: `tests/test_orchestrator_errors.py`.

## Changes made

- `src/agents/orchestrator.py`
  - `_run_sub_agent` now emits `agent_started` (agent, model, run_id) before the
    ADK Runner and `agent_finished` (agent, model, duration, run_id) after it
    returns. Exceptions still propagate loudly (no `finally`-masking), so a
    failing sub-agent is caught by the orchestrator's `except Exception` handler.
  - `_run_sub_agent` gained a `run_id` keyword (defaults to the run manager id)
    and every `OrchestratorTools` call site now passes `run_id=self.run_id`.
  - `run_orchestrator` gained `external_run_id: str | None = None` so the run id
    is deterministic in tests (and so a future T2/T5 wiring can pass the API run
    id straight through). Default preserves prior behavior.
  - The `except Exception` handler now emits `run_failed` with a `reason`
    (T2 requirement: "SSE yields run_failed with reason") before archiving.
- `src/events.py`
  - Added a per-run sink registry (`register_run_sink` / `unregister_run_sink` /
    `_run_sink_for`) so typed events routed by `run_id` reach only that run's
    channel (D4: no cross-run broadcast). `_emit_sync` now also delivers to the
    bound sink (alongside global subscribers), keeping the existing no-raise,
    fire-and-forget semantics.

## Verification (a) — sub-agent raise → failed + run_failed + process alive

Command:
```
venv/bin/python -m pytest tests/test_orchestrator_errors.py::test_sub_agent_raising_ends_run_failed_and_stays_alive -v
```

Output:
```
PASSED [ 66%]
```

The test fakes the ADK `Runner` to raise `Boom("sub-agent kaboom")` from the
researcher turn, registers a per-run sink on `run-fail-1`, runs
`run_orchestrator(...)` to completion (no exception escapes → process alive),
then asserts `result.status == "failed"`, `result.error` contains `Boom` and
`kaboom`, and that a `run_failed` event carrying the reason + run_id reached the
sink.

## Verification (b) — happy path emits start+finish for all 7 sub-agents in order

Command:
```
venv/bin/python -m pytest tests/test_orchestrator_errors.py::test_happy_path_emits_start_finish_for_seven_agents -v
```

Output:
```
PASSED [ 33%]
```

The test fakes `Runner` with a benign single-text-event generator and drives all
7 sub-agents via `_run_sub_agent`. It asserts 14 lifecycle events
(`agent_started`/`agent_finished` × 7) in the exact order
Researcher → Advocate → Critic → Creative → Judge → PRD Writer → Security Auditor,
each carrying the correct `model`, a `duration >= 0`, and the run_id.

## Auxiliary verification

Command:
```
venv/bin/python -m pytest tests/test_orchestrator_errors.py::test_sub_agent_exception_propagates_from_runner -v
```
Output:
```
PASSED [100%]
```
Confirms a sub-agent exception still propagates (agent_started emitted, no
agent_finished), so failures are never silently swallowed at the sub-agent level.

## Full test suite

Command:
```
venv/bin/python -m pytest tests/ -q
```
Output (final line):
```
171 passed in 3.72s
```

## Notes / deviations

- No deviation from T2 scope. `external_run_id` is additive (optional kwarg) and
  only exists to make the run id deterministic and ready for T5's ACK/TTL wiring.
- The per-run sink in `events.py` is the minimal bridge needed to prove "SSE
  yields run_failed" without a live SSE client; it respects D4 (no cross-run
  broadcast).
