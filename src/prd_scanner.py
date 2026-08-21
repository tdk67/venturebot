"""PRD Completeness Scanner — deterministic quality gate (P0.3).

Checks a PRD document for:
- All required sections present
- Every functional requirement has at least one acceptance criterion
- Security/auth/data-handling section present
- No unsourced factual claims (heuristic: numbers/dates/product names without URLs)

This runs BEFORE the orchestrator can present to the human.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass
class PrdFinding:
    """A single issue found by the PRD scanner."""
    section: str
    issue: str
    severity: Literal["critical", "high", "medium", "low"]
    line: int | None = None


@dataclass
class PrdScanResult:
    """Result of scanning a PRD document."""
    verdict: Literal["PASS", "FLAG"]
    findings: list[PrdFinding]
    sections_found: list[str]
    sections_missing: list[str]


# Required sections in the PRD (case-insensitive matching)
REQUIRED_SECTIONS = {
    "product overview": "Product Overview",
    "functional requirements": "Functional Requirements",
    "non-functional requirements": "Non-Functional Requirements",
    "technical architecture": "Technical Architecture",
    "acceptance criteria": "Acceptance Criteria",
    "milestones": "Milestones",
    "risks": "Risks",
}

# Security-related section keywords
SECURITY_KEYWORDS = [
    "security",
    "authentication",
    "authorization",
    "data handling",
    "privacy",
    "gdpr",
    "encryption",
    "access control",
]


def scan_prd(prd_text: str) -> PrdScanResult:
    """Scan a PRD document for completeness issues.
    
    Returns a PrdScanResult with verdict PASS or FLAG and a list of findings.
    """
    findings: list[PrdFinding] = []
    sections_found: list[str] = []
    sections_missing: list[str] = []
    
    lines = prd_text.split("\n")
    
    # 1. Check required sections
    _check_required_sections(prd_text, lines, sections_found, sections_missing, findings)
    
    # 2. Check functional requirements have acceptance criteria
    _check_fr_acceptance_criteria(prd_text, lines, findings)
    
    # 3. Check security/auth/data-handling section present
    _check_security_section(prd_text, findings)
    
    # 4. Check for unsourced factual claims (numbers/dates without URLs)
    _check_unsourced_claims(lines, findings)
    
    # Determine verdict
    verdict = "FLAG" if findings else "PASS"
    
    return PrdScanResult(
        verdict=verdict,
        findings=findings,
        sections_found=sections_found,
        sections_missing=sections_missing,
    )


def _check_required_sections(
    prd_text: str,
    lines: list[str],
    sections_found: list[str],
    sections_missing: list[str],
    findings: list[PrdFinding],
) -> None:
    """Check that all required sections are present."""
    prd_lower = prd_text.lower()
    
    for keyword, display_name in REQUIRED_SECTIONS.items():
        if keyword in prd_lower:
            sections_found.append(display_name)
        else:
            sections_missing.append(display_name)
            findings.append(PrdFinding(
                section=display_name,
                issue=f"Missing required section: {display_name}",
                severity="critical",
            ))


def _check_fr_acceptance_criteria(
    prd_text: str,
    lines: list[str],
    findings: list[PrdFinding],
) -> None:
    """Check that functional requirements have acceptance criteria."""
    # Find functional requirements section
    fr_start = None
    fr_end = None
    for i, line in enumerate(lines):
        if re.match(r"^#+\s*functional\s+requirements", line, re.IGNORECASE):
            fr_start = i
        elif fr_start is not None and re.match(r"^#+\s", line):
            fr_end = i
            break
    
    if fr_start is None:
        return  # Already flagged by section check
    
    if fr_end is None:
        fr_end = len(lines)
    
    # Look for acceptance criteria in the FR section
    fr_section = "\n".join(lines[fr_start:fr_end])
    has_acceptance = (
        "acceptance criteria" in fr_section.lower()
        or "given/when/then" in fr_section.lower()
        or "should" in fr_section.lower()
        or "must" in fr_section.lower()
    )
    
    if not has_acceptance:
        findings.append(PrdFinding(
            section="Functional Requirements",
            issue="Functional requirements lack acceptance criteria (use Given/When/Then or should/must statements)",
            severity="high",
            line=fr_start + 1,
        ))


def _check_security_section(prd_text: str, findings: list[PrdFinding]) -> None:
    """Check that security/auth/data-handling is covered."""
    prd_lower = prd_text.lower()
    has_security = any(kw in prd_lower for kw in SECURITY_KEYWORDS)
    
    if not has_security:
        findings.append(PrdFinding(
            section="Security",
            issue="PRD lacks security/authentication/data-handling section. Add a section covering auth, data privacy, and security requirements.",
            severity="critical",
        ))


def _check_unsourced_claims(lines: list[str], findings: list[PrdFinding]) -> None:
    """Check for unsourced factual claims (heuristic: numbers/dates without URLs on same line)."""
    # Patterns that suggest factual claims needing sources
    claim_patterns = [
        r"\$\d+[MBK]?\b",  # Dollar amounts: $10M, $5B, $100K
        r"\b\d+\s*(million|billion|trillion)\b",  # X million/billion
        r"\b\d{4}-\d{2}-\d{2}\b",  # ISO dates
        r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{4}\b",  # Month Year
        r"\b\d+%\b",  # Percentages
    ]
    
    url_pattern = re.compile(r"https?://\S+|\[.*?\]\(.*?\)", re.IGNORECASE)
    
    for i, line in enumerate(lines, 1):
        # Skip lines that are headers, lists of URLs, or code blocks
        if line.strip().startswith("#") or line.strip().startswith("```"):
            continue
        
        # Check if line contains a factual claim
        has_claim = False
        for pattern in claim_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                has_claim = True
                break
        
        # If line has a claim but no URL, flag it
        if has_claim and not url_pattern.search(line):
            findings.append(PrdFinding(
                section="Source Verification",
                issue=f"Line contains a factual claim (number/date/percentage) without a source URL: {line.strip()[:100]}",
                severity="medium",
                line=i,
            ))


def format_scan_result(result: PrdScanResult) -> str:
    """Format the scan result as a human-readable string."""
    if result.verdict == "PASS":
        return "✅ PRD Scanner: PASS — all required sections present and complete."
    
    lines = [f"⚠️ PRD Scanner: FLAG — {len(result.findings)} issue(s) found:\n"]
    
    critical_count = sum(1 for f in result.findings if f.severity == "critical")
    high_count = sum(1 for f in result.findings if f.severity == "high")
    medium_count = sum(1 for f in result.findings if f.severity == "medium")
    low_count = sum(1 for f in result.findings if f.severity == "low")
    
    lines.append(f"  Critical: {critical_count}, High: {high_count}, Medium: {medium_count}, Low: {low_count}\n")
    
    if result.sections_missing:
        lines.append(f"  Missing sections: {', '.join(result.sections_missing)}\n")
    
    for finding in result.findings:
        line_info = f" (line {finding.line})" if finding.line else ""
        lines.append(f"  [{finding.severity.upper()}] {finding.section}{line_info}: {finding.issue}\n")
    
    return "".join(lines)
