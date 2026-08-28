"""Run manager  -- S2 kill switch + dead-man ceiling.

A run is a cancellable unit of work. The loop (or ADK runner) polls
`should_stop()` between every agent turn and between LLM calls. `stop()` is
callable from the UI/API and from an in-process signal; the dead-man ceiling
forces termination even if nobody is watching.

The cancellation flag lives in-process (threading.Event) AND is persisted to
state.json so the dashboard reflects it. The in-process Event is authoritative
for actually halting work; the persisted status is for observability.
"""
from __future__ import annotations

import threading
import time

from . import config


class RunCancelled(Exception):
    """Raised when the run is cancelled mid-flight."""


class _Manager:
    def __init__(self):
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}
        self._deadlines: dict[str, float] = {}
        self._last_run_id: str | None = None

    def start(self, run_id: str, deadline_seconds: int | None = None) -> None:
        with self._lock:
            evt = threading.Event()
            self._events[run_id] = evt
            self._deadlines[run_id] = time.monotonic() + (
                deadline_seconds if deadline_seconds is not None
                else config.RUN_DEADLINE_SECONDS
            )
            self._last_run_id = run_id

    def stop(self, reason: str = "user requested stop", run_id: str | None = None) -> None:
        with self._lock:
            target_id = run_id or self._last_run_id
            if target_id and target_id in self._events:
                self._events[target_id].set()
            else:
                for evt in self._events.values():
                    evt.set()

    def should_stop(self, run_id: str | None = None) -> bool:
        """True if cancelled OR dead-man ceiling reached. Cheap to poll."""
        with self._lock:
            target_id = run_id or self._last_run_id
            if not target_id:
                return False
            evt = self._events.get(target_id)
            if evt and evt.is_set():
                return True
            dl = self._deadlines.get(target_id)
            if dl is not None and time.monotonic() >= dl:
                if evt:
                    evt.set()  # latch
                return True
            return False

    def check(self, run_id: str | None = None) -> None:
        """Poll point: raise RunCancelled if we should stop."""
        if self.should_stop(run_id):
            raise RunCancelled("run cancelled or deadline reached")

    def deadline_reached(self, run_id: str | None = None) -> bool:
        with self._lock:
            target_id = run_id or self._last_run_id
            if not target_id:
                return False
            dl = self._deadlines.get(target_id)
            return dl is not None and time.monotonic() >= dl

    def finish(self, run_id: str) -> None:
        with self._lock:
            self._events.pop(run_id, None)
            self._deadlines.pop(run_id, None)
            if self._last_run_id == run_id:
                self._last_run_id = None

    @property
    def run_id(self) -> str | None:
        with self._lock:
            return self._last_run_id


manager = _Manager()
