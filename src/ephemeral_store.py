"""T5 — Ephemeral run store with TTL + ACK (REWRITE_PLAN.md Part C, T5 / A-S6 / S7 / D2 / D3).

Replaces the plain in-memory registry with a store that owns the full run
lifecycle:

  * D2 — only per-run RunRecord objects are held; there is no idea table, no
    user table, no lessons. There is deliberately NO listing/enumeration API
    (S3): lookups are strictly by unguessable run_id.
  * D3 / S7 — a finished result is held server-side until the client ACKs the
    download (or the TTL expires). After ACK or expiry the record is removed
    and marked "gone", so `GET result` returns 410 rather than 404.
  * S6 — every record carries a TTL; a periodic sweeper (`sweep_ttl`) removes
    expired runs. Idea text/PRD/research live only in the ephemeral record and
    the per-run workspace (which `src/inflight_sweeper.py` wipes).

Acked/expired records are parked in a small in-memory tombstone set (`_gone`,
run_ids only — no content) purely so the API can answer 410 instead of 404.
Unknown ids stay 404 (S3).

The store holds records in a per-(running)IP box so a future concurrency cap
can group by client IP without ever exposing content across runs. In practice
T5 keeps it simple: records are keyed by run_id in a flat map.
"""
from __future__ import annotations

import time
from typing import Callable

from . import config

# Optional callback(run_id, event, data) fired when a run is dropped by TTL.
# The dashboard uses it to surface an `expired` transient event to any still-open
# SSE stream. Plugins MUST be resilient (never raise).
_Emit = Callable[[str, str, dict], None] | None


class EphemeralStore:
    """In-memory, TTL-bounded, ACK-aware store of per-run Record objects.

    Public surface (matches the tests):
      * `register(record, ip=..., now=...)`  — add a live run
      * `get(run_id)`                        — live record or None
      * `ack(run_id, now=...)`               — True if this run was acked/removed
      * `sweep_ttl(now=...)`                 — returns list of swept run_ids
      * `is_gone(run_id)`                    — True if acked/expired (-> 410)
      * `clear()`                            — reset everything
      * `.tick`                              — injectable clock (callable->float)
    """

    def __init__(self, watch_plugin: _Emit = None, ttl_seconds: float | None = None):
        self._records: dict[str, "object"] = {}   # run_id -> RunRecord
        self._created: dict[str, float] = {}       # run_id -> ts
        self._gone: dict[str, bool] = {}           # tombstone run_ids (no content)
        self._watch = watch_plugin
        self.ttl = ttl_seconds if ttl_seconds is not None else float(getattr(config, "RUN_TTL_SECONDS", 24 * 3600))
        self.tick: Callable[[], float] = time.time  # default; tests override

    def _ts(self) -> float:
        return self.tick()

    # -- lifecycle ----------------------------------------------------------

    def register(self, record: "object", *, now: float | None = None) -> None:
        """Register a live run record. This is the ONLY way a run enters the
        store. There is deliberately no listing/iteration of the store (S3)."""
        ts = now if now is not None else self._ts()
        run_id = getattr(record, "run_id", None)
        if not run_id:
            raise ValueError("run record must expose a run_id")
        self._records[run_id] = record
        self._created[run_id] = ts

    def get(self, run_id: str) -> "object | None":
        """Return the live record, or None if absent/acked/expired."""
        return self._records.get(run_id)

    def ack(self, run_id: str, *, now: float | None = None) -> bool:
        """A client acknowledges the download. Removes the record and tombstones
        it (so the result endpoint returns 410, not 404). Returns True if this
        call caused the removal (the record existed), False if already gone."""
        if run_id in self._records:
            self._records.pop(run_id, None)
            self._created.pop(run_id, None)
            self._tombstone(run_id)
            return True
        return run_id in self._gone

    def sweep_ttl(self, *, now: float | None = None) -> list[str]:
        """Remove every record past its TTL. Returns the swept run_ids."""
        ts = now if now is not None else self._ts()
        swept: list[str] = []
        for run_id, created in list(self._created.items()):
            if ts - created >= self.ttl:
                self._records.pop(run_id, None)
                self._created.pop(run_id, None)
                self._tombstone(run_id)
                swept.append(run_id)
        return swept

    def is_gone(self, run_id: str) -> bool:
        return run_id in self._gone

    def clear(self) -> None:
        self._records.clear()
        self._created.clear()
        self._gone.clear()

    # -- internals -----------------------------------------------------------

    def _tombstone(self, run_id: str) -> None:
        self._gone[run_id] = True
        if self._watch is not None:
            try:
                self._watch(run_id, "expired", {"run_id": run_id})
            except Exception:
                pass  # the watcher is fire-and-forget