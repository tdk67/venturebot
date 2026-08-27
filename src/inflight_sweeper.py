"""T5 — Ephemeral workspace sweeper (REWRITE_PLAN.md Part A, S6 / D2).

S6 requires that idea/research/PRD text never persists on disk server-side
except inside the ephemeral per-run workspace, which is wiped at run end and
by the TTL sweeper. This module owns that wipe.

The orchestrator writes everything under `config.WORKSPACE_DIR / runs/{run_id}/`
(see `src/agents/orchestrator.py::_workspace_dir`). When a run is acked or
expires from the ephemeral store, `sweep_run_workspace` removes that per-run
directory so a forensic `grep -r` of the workspace tree finds zero idea text.

Only the exact run's directory is removed; the glob root stays in place so the
rest of the tree is untouched.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import config


def sweep_run_workspace(run_id: str, *, root: Path | None = None) -> bool:
    """Remove one run's workspace directory. Returns True if something existed
    and was removed, False if already absent. Never raises on a missing dir.

    `root` defaults to the configured WORKSPACE_DIR so the sweep targets the
    same tree the orchestrator writes to (isolated in tests via monkeypatch).
    """
    base = root or config.WORKSPACE_DIR
    ws = (base / "runs" / run_id).resolve()
    runs_root = (base / "runs").resolve()
    # Guard: only ever touch a direct child of runs/ (never the runs/ root, and
    # never allow a run_id to walk up via .. ).
    if ws.parent != runs_root:
        caps = ws.name
        return False
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
        return True
    return False


def sweep_workspaces(run_ids: "list[str] | set[str]", *, root: Path | None = None) -> int:
    """Sweep a collection of run_ids. Returns how many dirs were actually
    removed. Safe for sets/iterables; skips ids that are absent."""
    removed = 0
    for run_id in run_ids:
        if sweep_run_workspace(run_id, root=root):
            removed += 1
    return removed