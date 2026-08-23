"""File-backed JSON state store — single source of truth for observability.

Every module writes through this. State persists to config.STATE_FILE with
atomic writes. This replaces the legacy sim_store.py (archived).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import uuid

from . import config

INITIAL_TASKS = [
    {"id": "t1", "title": "Research Agent", "status": "todo", "assignee": "Researcher"},
    {"id": "t2", "title": "Advocate argument", "status": "todo", "assignee": "Advocate"},
    {"id": "t3", "title": "Critic rebuttal", "status": "todo", "assignee": "Critic"},
    {"id": "t4", "title": "Judge verdict", "status": "todo", "assignee": "Judge"},
    {"id": "t5", "title": "PRD Writer", "status": "todo", "assignee": "PRD Writer"},
]

VALID_STATUSES = {"idle", "running", "approved", "failed", "stopped", "waiting_user"}


def _initial_state() -> dict:
    return {
        "run_id": None,
        "status": "idle",
        "iteration": 0,
        "messages": [
            {
                "timestamp": time.strftime("%H:%M:%S"),
                "agent": "System",
                "model": "core",
                "message": "VentureBot initialized.",
            }
        ],
        "tasks": [dict(t) for t in INITIAL_TASKS],
        "workspace": {"files": []},
        "budget": None,
    }


def load_state() -> dict:
    if not os.path.exists(config.STATE_FILE):
        state = _initial_state()
        save_state(state)
        return state
    try:
        with open(config.STATE_FILE) as f:
            data = json.load(f)
        for key, default in _initial_state().items():
            data.setdefault(key, default)
        return data
    except (json.JSONDecodeError, OSError):
        state = _initial_state()
        save_state(state)
        return state


def save_state(state: dict) -> None:
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(config.STATE_FILE) or "."
    )
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, config.STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def reset_state() -> dict:
    state = _initial_state()
    save_state(state)
    return state


def start_run() -> dict:
    state = load_state()
    state["run_id"] = uuid.uuid4().hex[:12]
    state["status"] = "running"
    state["iteration"] = 0
    state["tasks"] = [dict(t) for t in INITIAL_TASKS]
    state["workspace"] = {"files": []}
    state["messages"] = [
        {
            "timestamp": time.strftime("%H:%M:%S"),
            "agent": "System",
            "model": "core",
            "message": f"Run {state['run_id']} started.",
        }
    ]
    save_state(state)
    return state


def set_status(status: str) -> dict:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    state = load_state()
    state["status"] = status
    save_state(state)
    return state


def log(agent: str, model: str, message: str) -> dict:
    state = load_state()
    state["messages"].append(
        {
            "timestamp": time.strftime("%H:%M:%S"),
            "agent": agent,
            "model": model,
            "message": message,
        }
    )
    save_state(state)
    print(f"[{state['messages'][-1]['timestamp']}] ({agent} / {model}): {message}")
    return state


def update_task(task_id: str, status: str) -> dict:
    state = load_state()
    for t in state["tasks"]:
        if t["id"] == task_id:
            t["status"] = status
    save_state(state)
    return state


def set_iteration(n: int) -> dict:
    state = load_state()
    state["iteration"] = n
    save_state(state)
    return state


def set_workspace_files(files: list[str]) -> dict:
    state = load_state()
    state["workspace"]["files"] = files
    save_state(state)
    return state
