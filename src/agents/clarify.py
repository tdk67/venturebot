"""Custom HITL tool: clarify_question — pause and ask the human.

ADK's LongRunningFunctionTool pauses the invocation and emits an
input-required event when the function is called. The human's answer is
injected as the tool result when the session resumes.
"""
from __future__ import annotations

from typing import Any


def clarify_question(question: str) -> str:
    """Ask the human ONE specific question and wait for the answer.

    Wrapped by LongRunningFunctionTool so ADK pauses the invocation. The
    returned string is the placeholder shown while awaiting human input.
    """
    return question  # surfaced to the human; answer injected on resume
