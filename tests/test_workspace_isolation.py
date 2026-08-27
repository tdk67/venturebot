"""A1 (W4a): per-run workspace isolation + traversal guard tests.

Each orchestrator run must read/write ONLY its own workspace/runs/{run_id}/ dir.
Path traversal and cross-run access must be blocked.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents import orchestrator as orch


def test_workspace_dir_is_per_run(tmp_path, monkeypatch):
    monkeypatch.setattr(orch.config, "WORKSPACE_DIR", tmp_path)
    a = orch._workspace_dir("run-aaa")
    b = orch._workspace_dir("run-bbb")
    assert a != b
    assert a == tmp_path / "runs" / "run-aaa"
    assert b == tmp_path / "runs" / "run-bbb"


def test_run_id_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(orch.config, "WORKSPACE_DIR", tmp_path)
    # Hostile run_id cannot escape the runs/ root.
    ws = orch._workspace_dir("../../etc")
    assert tmp_path / "runs" in ws.parents or ws.parent == tmp_path / "runs"
    assert ".." not in ws.parts[len(tmp_path.parts):]


def test_write_and_read_roundtrip_scoped_to_run(tmp_path, monkeypatch):
    monkeypatch.setattr(orch.config, "WORKSPACE_DIR", tmp_path)
    assert orch._write_workspace_file("PRD.md", "hello", run_id="r1") == "ok"
    assert orch._read_workspace_file("PRD.md", run_id="r1") == "hello"
    # Another run sees nothing of it.
    assert orch._read_workspace_file("PRD.md", run_id="r2") is None


def test_traversal_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(orch.config, "WORKSPACE_DIR", tmp_path)
    secret = tmp_path / "runs" / "victim" / "secret.txt"
    secret.parent.mkdir(parents=True)
    secret.write_text("other user's PRD")

    # Relative traversal from attacker's run.
    assert orch._write_workspace_file("../victim/evil.txt", "x", run_id="atk") != "ok"
    assert orch._read_workspace_file("../victim/secret.txt", run_id="atk") is None

    # Absolute path escape.
    assert orch._read_workspace_file(str(secret), run_id="atk") is None
    assert orch._write_workspace_file(str(secret), "owned", run_id="atk") != "ok"

    # The victim file is untouched.
    assert secret.read_text() == "other user's PRD"


def test_subdirectory_paths_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(orch.config, "WORKSPACE_DIR", tmp_path)
    assert orch._write_workspace_file("docs/notes/idea.md", "fine", run_id="r1") == "ok"
    assert orch._read_workspace_file("docs/notes/idea.md", run_id="r1") == "fine"


def test_tools_object_uses_run_scope(tmp_path, monkeypatch):
    """The OrchestratorTools file tools must resolve against the run's own dir."""
    monkeypatch.setattr(orch.config, "WORKSPACE_DIR", tmp_path)
    tools = orch.OrchestratorTools(
        result=orch.OrchestratorResult(idea="x"),
        session_service=None,
        sid="",
        user_id="user",
        inbox=None,
        run_id="tools-run",
        agents=None,
    )
    import asyncio

    out = asyncio.run(tools.write_file("PRD.md", "# PRD"))
    assert out == "ok"
    assert (tmp_path / "runs" / "tools-run" / "PRD.md").exists()

    # A second tools object with a different run_id must not see the file.
    other = orch.OrchestratorTools(
        result=orch.OrchestratorResult(idea="y"),
        session_service=None,
        sid="",
        user_id="user",
        inbox=None,
        run_id="other-run",
    )
    out = asyncio.run(other.read_file("PRD.md"))
    assert out.startswith("File not found")
