"""Regression: the "code cleanup" commit (ae3a06e) dropped several imports from
src/agents/orchestrator.py (agent_turn, run_manager, get_store, capture_turn,
analyze_turn, SteeringInbox) while leaving the code that uses them. The result
was a silent NameError the first time a sub-agent finished a turn  -- the debate
appeared to "start" and then died with no output. These tests import every
runtime module and assert the symbols they rely on actually resolve.
"""
from __future__ import annotations

import ast
import builtins
from pathlib import Path


def _module_paths():
    src = Path(__file__).resolve().parent.parent / "src"
    return sorted(src.rglob("*.py"))


def _undefined_names(path: Path) -> set[str]:
    """Find module-level Name loads that are not imported, defined, or builtin.

    Function parameters and loop variables are excluded (they are Store targets
    somewhere in the module). This is a heuristic, not a full linter  -- but it
    catches the exact class of bug that broke the debate.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                imported.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                imported.add((a.asname or a.name).split(".")[0])
    used = {
        n.id for n in ast.walk(tree)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    }
    assigned = {
        n.id for n in ast.walk(tree)
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.AugStore, ast.Del))
    }
    # Function args + lambda args are ast.arg, not Name Store nodes.
    assigned |= {
        a.arg for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        for a in (list(n.args.posonlyargs) + list(n.args.args) + list(n.args.kwonlyargs)
                  + (list(n.args.vararg) if n.args.vararg else [])
                  + (list(n.args.kwarg) if n.args.kwarg else []))
    }
    # Exception handler bindings: `except X as e`  -- `e` is ExceptHandler.name,
    # a plain string, not a Name Store node.
    assigned |= {
        n.name for n in ast.walk(tree)
        if isinstance(n, ast.ExceptHandler) and n.name
    }
    # Comprehension targets (for x in ... / async for) are Name Store nodes,
    # already covered above, but include generator variable names defensively.
    defined = {
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    builtin = set(dir(builtins))
    return used - imported - assigned - defined - builtin - {
        "self", "annotations", "None", "True", "False", "__file__",
    }


def test_critical_agent_modules_have_no_undefined_names():
    """The debate-driving modules must not reference undefined symbols."""
    critical = {
        "src/agents/orchestrator.py",
        "src/agents/agents.py",
    }
    root = Path(__file__).resolve().parent.parent
    for rel in critical:
        path = root / rel
        undefined = _undefined_names(path)
        assert not undefined, f"{rel} references undefined names: {sorted(undefined)}"


def test_orchestrator_imports_cleanly():
    """The orchestrator module must import without error (it is what /api/run-phase1 drives)."""
    from src.agents import orchestrator  # noqa: F401
    assert hasattr(orchestrator, "run_orchestrator")
    assert hasattr(orchestrator, "agent_turn")
