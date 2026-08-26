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

from . import config, store


class RunCancelled(Exception):
    """Raised when the run is cancelled mid-flight."""


class _Manager:
    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._run_id: str | None = None
        self._deadline: float | None = None

    def start(self, run_id: str, deadline_seconds: int | None = None) -> None:
        with self._lock:
            self._event.clear()
            self._run_id = run_id
            self._deadline = time.monotonic() + (
                deadline_seconds if deadline_seconds is not None
                else config.RUN_DEADLINE_SECONDS
            )

    def stop(self, reason: str = "user requested stop") -> None:
        with self._lock:
            self._event.set()
        store.set_status("stopped")
        store.log("System", "core", f"Run cancelled: {reason}")

    def should_stop(self) -> bool:
        """True if cancelled OR dead-man ceiling reached. Cheap to poll."""
        with self._lock:
            if self._event.is_set():
                return True
            if self._deadline is not None and time.monotonic() >= self._deadline:
                self._event.set()  # latch: once triggered, stays triggered
                return True
            return False

    def check(self) -> None:
        """Poll point: raise RunCancelled if we should stop."""
        if self.should_stop():
            raise RunCancelled("run cancelled or deadline reached")

    def deadline_reached(self) -> bool:
        with self._lock:
            return self._deadline is not None and time.monotonic() >= self._deadline

    @property
    def run_id(self) -> str | None:
        return self._run_id


manager = _Manager()
