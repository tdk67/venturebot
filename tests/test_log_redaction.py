"""A3 (G4/W7): log redaction at source.

Debate content (idea text, transcripts, PRD drafts) must never reach
stdout/journald — the process log gets metadata only. state.json keeps the
full message for the dashboard feed.
"""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import store


def test_log_stdout_is_metadata_only(capsys):
    secret = "The idea is about a stealth startup called Acme with API key sk-123"
    store.log("Researcher", "gemini-test", secret)
    out = capsys.readouterr().out
    assert "Acme" not in out
    assert "sk-123" not in out
    assert "Researcher" in out  # metadata is fine
    assert f"{len(secret)} chars" in out

    # Dashboard feed still has the full text.
    state = store.load_state()
    assert state["messages"][-1]["message"] == secret


def test_log_no_content_leak_for_long_transcripts(capsys):
    transcript = "PRD draft: users pay $9/mo for the premium tier. " * 50
    store.log("Orchestrator", "core", transcript)
    out = capsys.readouterr().out
    assert "$9/mo" not in out
