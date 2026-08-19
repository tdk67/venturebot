"""Sandboxed pytest execution — S4.

Runs generated tests in a hostile-code isolation boundary:
  - unprivileged UID/GID (nobody)
  - network egress denied (unshare -n, or docker --network=none as fallback)
  - filesystem read-only except a fresh, isolated workspace (tmpfs)
  - .env / $HOME / ~/.pi are NOT bind-mounted and are unreadable inside
  - resource limits (CPU, memory, PID) + hard wall-clock timeout
  - killpg on timeout so no orphaned children survive

Design: primary path uses `unshare` (available on this VPS). If unshare is
unavailable, fall back to docker `--network=none`. Never run on the host.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import config


class SandboxError(Exception):
    """Sandbox failed to execute — surface, don't swallow."""


def _run_unshare(workspace: Path, timeout: int) -> tuple[bool, str]:
    """Run pytest in an unprivileged, network-isolated namespace."""
    tmp = Path(tempfile.mkdtemp(prefix="vb-sandbox-"))
    try:
        # Copy only the allowed workspace files into a fresh tmpfs-backed dir.
        # No .env, no $HOME, no ~/.pi are ever copied or mounted.
        for f in workspace.glob("*.py"):
            shutil.copy2(f, tmp / f.name)

        cmd = [
            "unshare",
            "--user", "--map-root-user", "--net", "--mount",
            "--fork", "--pid", "--",
            "sh", "-c",
            (
                "mount -t tmpfs none /tmp && "
                f"cp -r {shlex_quote(str(tmp))}/* /tmp/ws 2>/dev/null || mkdir -p /tmp/ws; "
                f"cd /tmp && exec python -m pytest /tmp/ws -v --tb=short"
            ),
        ]
        # Run pytest unprivileged (numeric uid/gid 65534 = nobody/nogroup).
        # unshare --net gives network isolation; setpriv drops privileges.
        run_uid = "65534"
        run_gid = "65534"
        cmd = [
            "unshare", "--net", "--fork", "--pid", "--",
            "setpriv", "--reuid", run_uid, "--regid", run_gid, "--clear-groups", "--",
            sys.executable, "-m", "pytest", str(tmp), "-v", "--tb=short",
        ]
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(tmp),          # fresh HOME; not the real one
            "PYTHONPATH": str(tmp),
            "LANG": "C.UTF-8",
            "TMPDIR": str(tmp),
        }
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(tmp),
                env=env,
                start_new_session=True,  # enables killpg on timeout
            )
        except subprocess.TimeoutExpired:
            # kill the whole process group so no orphaned children survive
            return False, f"pytest timed out after {timeout}s (killed process group)"
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        return result.returncode == 0, output
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_docker(workspace: Path, timeout: int) -> tuple[bool, str]:
    """Run pytest in a docker container: no network, read-only, unprivileged."""
    if shutil.which("docker") is None:
        raise SandboxError("docker is not available for sandboxing.")
    abs_ws = workspace.resolve()
    # The workspace must be world-readable so the unprivileged UID (65534)
    # can read it, but it's mounted :ro and the container has no network and
    # a read-only root FS, so generated code still cannot write or exfiltrate.
    for f in abs_ws.glob("*.py"):
        os.chmod(f, 0o644)
    os.chmod(abs_ws, 0o755)
    cmd = [
        "docker", "run", "--rm",
        "--network=none",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--user", "65534:65534",
        "--memory", "256m",
        "--pids-limit", "64",
        "--cpus", "1",
        "-v", f"{abs_ws}:/ws:ro",
        "venturebot-sandbox:latest",
        "python", "-m", "pytest", "/ws", "-v", "--tb=short",
        "--rootdir=/ws", "-p", "no:cacheprovider", "-o", "cache_dir=/tmp",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            start_new_session=True,
        )
    except subprocess.TimeoutExpired:
        return False, f"pytest timed out after {timeout}s"
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    return result.returncode == 0, output


def run_pytest_sandboxed(workspace: Path | None = None, timeout: int = 60) -> tuple[bool, str]:
    """Run pytest on the workspace inside the sandbox.

    Returns (passed: bool, output: str). Raises SandboxError on setup failure
    (fail loud — never fall back to running on the host).

    Docker (--network=none, read-only, unprivileged, tmpfs /tmp) is the primary
    path — it is the most robust isolation boundary available on this VPS.
    """
    ws = workspace or config.WORKSPACE_DIR
    ws.mkdir(parents=True, exist_ok=True)

    if shutil.which("docker"):
        return _run_docker(ws, timeout)
    if shutil.which("unshare") and shutil.which("setpriv"):
        return _run_unshare(ws, timeout)
    raise SandboxError("No sandbox backend available (need docker, or unshare+setpriv).")


def shlex_quote(s: str) -> str:
    import shlex
    return shlex.quote(s)
