"""Generated-code guard  -- S3 (output guard) + S10 (deterministic scanner).

Statistically inspect LLM-generated code BEFORE it is written to disk or
executed. Blocks dangerous constructs and hardcoded secrets.

Two layers:
  1. `scan_code()`   -- deterministic, AST + regex, no LLM. Runs on EVERY artifact.
  2. (S10) Security Auditor agent  -- semantic review, separate module.

Returns a verdict dict; `requires_approval` is True when anything is flagged.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field


@dataclass
class Finding:
    severity: str  # "block" | "warn"
    rule: str
    line: int
    detail: str


@dataclass
class Verdict:
    ok: bool
    requires_approval: bool
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "requires_approval": self.requires_approval,
            "findings": [
                {"severity": f.severity, "rule": f.rule, "line": f.line, "detail": f.detail}
                for f in self.findings
            ],
        }


# Allowlist for generated implementation code. Stdlib-only, and deliberately
# narrow  -- anything with I/O, subprocess, or networking is NOT allowed.
IMPLEMENTATION_ALLOWLIST = {
    # builtins available without import
    "abs", "all", "any", "bin", "bool", "bytes", "chr", "complex", "dict",
    "divmod", "enumerate", "filter", "float", "format", "frozenset", "hex",
    "int", "isinstance", "issubclass", "len", "list", "map", "max", "min",
    "oct", "ord", "pow", "range", "repr", "reversed", "round", "set", "slice",
    "sorted", "str", "sum", "tuple", "type", "zip",
    # stdlib modules permitted in generated code (pure computation only)
    "math", "statistics", "itertools", "functools", "collections", "datetime",
    "decimal", "fractions", "heapq", "bisect", "re", "string", "enum",
    "dataclasses", "typing", "json", "random",
}

# Imports allowed ONLY in generated TEST files (they may use pytest + venture)
TEST_ONLY_ALLOWLIST = {"pytest", "venture", *IMPLEMENTATION_ALLOWLIST}

# Names that are banned anywhere in generated code (even without import,
# e.g. via __import__('os') or getattr tricks).
BANNED_CALLS = {
    "eval", "exec", "compile", "__import__", "open", "input",
    "os.system", "os.popen", "os.spawn", "os.remove", "os.unlink",
    "subprocess.run", "subprocess.call", "subprocess.Popen", "subprocess.check_output",
    "shutil.rmtree", "shutil.copy", "shutil.move", "socket.socket",
    "sys.exit", "exit", "quit", "breakpoint",
}

# Secret patterns (shared with scripts/secret_scan.sh)
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"sk-or-v1-[A-Za-z0-9]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"AQ[0-9A-Za-z_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


class _ImportVisitor(ast.NodeVisitor):
    def __init__(self, allowlist: set[str], findings: list[Finding]):
        self.allowlist = allowlist
        self.findings = findings

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top not in self.allowlist:
                self.findings.append(
                    Finding("block", "import-not-allowed", node.lineno,
                            f"import '{alias.name}' is not in the allowlist")
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            top = node.module.split(".")[0]
            if top not in self.allowlist:
                self.findings.append(
                    Finding("block", "import-not-allowed", node.lineno,
                            f"from '{node.module}' import ... is not in the allowlist")
                )
        self.generic_visit(node)


class _CallVisitor(ast.NodeVisitor):
    def __init__(self, findings: list[Finding]):
        self.findings = findings

    def _name_of(self, node) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._name_of(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        return None

    def visit_Call(self, node: ast.Call):
        name = self._name_of(node.func)
        if name and name in BANNED_CALLS:
            self.findings.append(
                Finding("block", "banned-call", node.lineno,
                        f"call to '{name}' is forbidden in generated code")
            )
        self.generic_visit(node)


def scan_secrets(code: str) -> list[Finding]:
    findings = []
    for i, line in enumerate(code.splitlines(), 1):
        for pat in SECRET_PATTERNS:
            if pat.search(line):
                findings.append(
                    Finding("block", "hardcoded-secret", i,
                            f"line matches secret pattern: {line.strip()[:60]}...")
                )
                break
    return findings


def scan_code(code: str, *, mode: str = "implementation") -> Verdict:
    """Scan generated code. mode: 'implementation' or 'test'."""
    findings: list[Finding] = []
    allowlist = IMPLEMENTATION_ALLOWLIST if mode == "implementation" else TEST_ONLY_ALLOWLIST

    findings += scan_secrets(code)

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        findings.append(
            Finding("block", "syntax-error", e.lineno or 0, f"cannot parse: {e.msg}")
        )
        return Verdict(ok=False, requires_approval=True, findings=findings)

    _ImportVisitor(allowlist, findings).visit(tree)
    _CallVisitor(findings).visit(tree)

    blocks = [f for f in findings if f.severity == "block"]
    warns = [f for f in findings if f.severity == "warn"]
    return Verdict(
        ok=not blocks,
        requires_approval=bool(blocks or warns),
        findings=findings,
    )
