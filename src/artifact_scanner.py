"""Deterministic artifact scanner + proof-read gate  -- S10.

The automated version of the manual review discipline (secret grep  -> AST scan
 -> schema validation) that VentureBot itself was subjected to. Every generated
artifact  -- research brief, PRD, generated code  -- passes through `scan_artifact`
before advancing to the next stage or to a human.

Layers (each non-optional):
  1. secret regex scan (shared with guard.py)
  2. banned-construct / injection-marker scan for text artifacts
  3. (code artifacts) AST allowlist + banned-call scan via guard.scan_code

The LLM Security Auditor catches *semantic* problems; this scanner catches
*mechanical* ones. The proof-read gate surfaces findings for human decision  -- 
never silently auto-passes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import guard


@dataclass
class ScanFinding:
    severity: str          # "block" | "warn"
    category: str          # "secret" | "injection" | "banned-construct" | ...
    detail: str

    def to_dict(self) -> dict:
        return {"severity": self.severity, "category": self.category, "detail": self.detail}


@dataclass
class ScanResult:
    ok: bool
    findings: list[ScanFinding] = field(default_factory=list)

    @property
    def blocks(self) -> list[ScanFinding]:
        return [f for f in self.findings if f.severity == "block"]

    def to_dict(self) -> dict:
        return {"ok": self.ok, "findings": [f.to_dict() for f in self.findings]}


# Injection markers that should never appear in a *produced* artifact (residue
# from a prompt-injection that leaked into output). Deliberately narrow to avoid
# false positives on legitimately-quoted content.
_INJECTION_MARKERS = [
    "ignore all previous instructions",
    "ignore previous instructions",
    "disregard all instructions",
    "you are now",
    "developer message:",
    "system prompt:",
    "do not follow",
]


def scan_secrets(text: str) -> list[ScanFinding]:
    """Secret-pattern scan over a text artifact. Returns findings."""
    return [
        ScanFinding("block", "secret", f.detail)
        for f in guard.scan_secrets(text)
    ]


def scan_injection_residue(text: str) -> list[ScanFinding]:
    """Look for prompt-injection residue in a produced artifact."""
    low = text.lower()
    findings = []
    for marker in _INJECTION_MARKERS:
        idx = low.find(marker)
        if idx != -1:
            findings.append(
                ScanFinding("block", "injection",
                            f"prompt-injection residue: {marker!r}")
            )
    return findings


def scan_artifact(content: str, *, kind: str = "text") -> ScanResult:
    """Scan a generated artifact.

    kind: "text" (PRD, brief)  -> secret + injection scans.
          "code"              -> secret + injection + AST guard scans.
    """
    findings: list[ScanFinding] = []
    findings += scan_secrets(content)
    findings += scan_injection_residue(content)

    if kind == "code":
        verdict = guard.scan_code(content, mode="implementation")
        for f in verdict.findings:
            findings.append(ScanFinding(f.severity, f.rule, f.detail))

    blocks = [f for f in findings if f.severity == "block"]
    return ScanResult(ok=not blocks, findings=findings)


def proof_read_gate(scanner_ok: bool, audit_verdict: str | None,
                    findings: list[dict]) -> dict:
    """Combine scanner + LLM-auditor results into a single gate decision.

    A PASS requires: scanner clean AND auditor verdict == "PASS".
    Anything else is FLAG and must reach the human  -- never auto-passed.
    """
    audit_pass = (audit_verdict or "").upper() == "PASS"
    ok = scanner_ok and audit_pass
    return {
        "ok": ok,
        "scanner_ok": scanner_ok,
        "audit_verdict": audit_verdict,
        "findings": findings,
    }
