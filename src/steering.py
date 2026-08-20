"""Steering inbox — user control without disrupting the running loop.

The loop runs autonomously between checkpoints. At each checkpoint it drains
this inbox and applies what arrived:

  - steering messages: free-text guidance injected into the NEXT agent's input
  - research URLs: user-provided links, fetched and fed to the Researcher
  - new ideas: queued for future runs (never touched mid-loop)

User messages NEVER interrupt an in-flight agent turn — they wait until the
next checkpoint, so the loop is never corrupted mid-generation.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class SteeringInbox:
    steering: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    new_ideas: list[str] = field(default_factory=list)

    def __post_init__(self):
        self._lock = threading.Lock()

    def add_steering(self, text: str) -> None:
        text = text.strip()
        if text:
            with self._lock:
                self.steering.append(text)

    def add_urls(self, urls: list[str]) -> None:
        clean = [u.strip() for u in urls if u.strip().startswith(("http://", "https://"))]
        if clean:
            with self._lock:
                self.urls.extend(clean)

    def add_idea(self, idea: str) -> None:
        idea = idea.strip()
        if idea:
            with self._lock:
                self.new_ideas.append(idea)

    def drain_steering(self) -> list[str]:
        """Take all pending steering messages (for injection at a checkpoint)."""
        with self._lock:
            out = self.steering
            self.steering = []
            return out

    def drain_urls(self) -> list[str]:
        with self._lock:
            out = self.urls
            self.urls = []
            return out

    def drain_ideas(self) -> list[str]:
        with self._lock:
            out = self.new_ideas
            self.new_ideas = []
            return out

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "pending_steering": len(self.steering),
                "pending_urls": len(self.urls),
                "pending_ideas": len(self.new_ideas),
                "new_ideas": list(self.new_ideas),
            }
