"""Tests for PRD Completeness Scanner (P0.3)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.prd_scanner import scan_prd, format_scan_result


def test_complete_prd_passes():
    """A PRD with all required sections, acceptance criteria, security, and sources passes."""
    prd = """# Product Overview
This is a great product.

# Functional Requirements
- User can log in. Acceptance criteria: Given valid credentials, When user clicks login, Then dashboard loads.
- User can create items. Should complete within 2 seconds.

# Non-Functional Requirements
- System handles 1000 concurrent users (source: https://example.com/load-test)

# Technical Architecture
Built with FastAPI and PostgreSQL.

# Acceptance Criteria
All FRs have acceptance criteria above.

# Milestones
MVP in 4 weeks.

# Risks
Risk of scope creep.

# Security
Authentication via OAuth2. Data encrypted at rest. GDPR compliant.
"""
    result = scan_prd(prd)
    assert result.verdict == "PASS"
    assert len(result.findings) == 0


def test_missing_required_section_flags_critical():
    """Missing a required section produces a critical finding."""
    prd = """# Product Overview
This is a great product.

# Functional Requirements
- User can log in.

# Technical Architecture
Built with FastAPI.

# Milestones
MVP in 4 weeks.

# Risks
Risk of scope creep.

# Security
Authentication via OAuth2.
"""
    result = scan_prd(prd)
    assert result.verdict == "FLAG"
    
    # Should flag missing Non-Functional Requirements and Acceptance Criteria
    critical_findings = [f for f in result.findings if f.severity == "critical"]
    assert len(critical_findings) >= 2
    
    sections_missing = [f.issue for f in critical_findings if "Missing required section" in f.issue]
    assert any("Non-Functional Requirements" in s for s in sections_missing)
    assert any("Acceptance Criteria" in s for s in sections_missing)


def test_fr_without_acceptance_criteria_flags_high():
    """Functional requirements without acceptance criteria produce a high-severity finding."""
    prd = """# Product Overview
This is a great product.

# Functional Requirements
- User can log in.
- User can create items.
- User can delete items.

# Non-Functional Requirements
- System handles 1000 concurrent users.

# Acceptance Criteria
All FRs must work.

# Technical Architecture
Built with FastAPI.

# Milestones
MVP in 4 weeks.

# Risks
Risk of scope creep.

# Security
Authentication via OAuth2.
"""
    result = scan_prd(prd)
    assert result.verdict == "FLAG"
    
    high_findings = [f for f in result.findings if f.severity == "high"]
    assert len(high_findings) >= 1
    assert any("acceptance criteria" in f.issue.lower() for f in high_findings)


def test_missing_security_section_flags_critical():
    """PRD without security/auth/data-handling section produces a critical finding."""
    prd = """# Product Overview
This is a great product.

# Functional Requirements
- User can log in. Acceptance criteria: should work.

# Non-Functional Requirements
- System handles 1000 concurrent users.

# Technical Architecture
Built with FastAPI.

# Acceptance Criteria
All FRs must work.

# Milestones
MVP in 4 weeks.

# Risks
Risk of scope creep.
"""
    result = scan_prd(prd)
    assert result.verdict == "FLAG"
    
    critical_findings = [f for f in result.findings if f.severity == "critical"]
    security_findings = [f for f in critical_findings if "security" in f.section.lower()]
    assert len(security_findings) >= 1


def test_unsourced_numbers_flag_medium():
    """Numbers/dates without source URLs produce medium-severity findings."""
    prd = """# Product Overview
This is a great product.

# Functional Requirements
- User can log in. Acceptance criteria: should work.

# Non-Functional Requirements
- System handles 1000 concurrent users.
- Market size is $10B by 2025.
- 50% of users prefer mobile.

# Technical Architecture
Built with FastAPI.

# Acceptance Criteria
All FRs must work.

# Milestones
MVP in 4 weeks.

# Risks
Risk of scope creep.

# Security
Authentication via OAuth2.
"""
    result = scan_prd(prd)
    
    # Should flag unsourced numbers/dates
    medium_findings = [f for f in result.findings if f.severity == "medium"]
    assert len(medium_findings) >= 1
    assert any("factual claim" in f.issue.lower() for f in medium_findings)


def test_sourced_numbers_pass():
    """Numbers with source URLs do not produce findings."""
    prd = """# Product Overview
This is a great product.

# Functional Requirements
- User can log in. Acceptance criteria: should work.

# Non-Functional Requirements
- System handles 1000 concurrent users (https://example.com/load-test).
- Market size is $10B by 2025 (source: https://example.com/report).

# Technical Architecture
Built with FastAPI.

# Acceptance Criteria
All FRs must work.

# Milestones
MVP in 4 weeks.

# Risks
Risk of scope creep.

# Security
Authentication via OAuth2.
"""
    result = scan_prd(prd)
    
    # Should not flag sourced numbers
    medium_findings = [f for f in result.findings if f.severity == "medium"]
    assert len(medium_findings) == 0


def test_sections_found_and_missing_tracked():
    """Scanner tracks which sections were found and which are missing."""
    prd = """# Product Overview
This is a great product.

# Functional Requirements
- User can log in.

# Security
Authentication via OAuth2.
"""
    result = scan_prd(prd)
    
    assert "Product Overview" in result.sections_found
    assert "Functional Requirements" in result.sections_found
    
    assert "Non-Functional Requirements" in result.sections_missing
    assert "Acceptance Criteria" in result.sections_missing
    assert "Technical Architecture" in result.sections_missing
    assert "Milestones" in result.sections_missing
    assert "Risks" in result.sections_missing


def test_format_scan_result_pass():
    """Format scan result for PASS verdict."""
    prd = """# Product Overview
Great product.

# Functional Requirements
- Login. Acceptance criteria: should work.

# Non-Functional Requirements
- Fast.

# Technical Architecture
FastAPI.

# Acceptance Criteria
All work.

# Milestones
4 weeks.

# Risks
Scope creep.

# Security
OAuth2.
"""
    result = scan_prd(prd)
    formatted = format_scan_result(result)
    assert "PASS" in formatted


def test_format_scan_result_flag():
    """Format scan result for FLAG verdict."""
    prd = """# Product Overview
Great product.
"""
    result = scan_prd(prd)
    formatted = format_scan_result(result)
    assert "FLAG" in formatted
    assert "Critical:" in formatted
    assert "Missing sections:" in formatted


def test_empty_prd_flags_all_missing():
    """An empty PRD flags all required sections as missing."""
    prd = ""
    result = scan_prd(prd)
    assert result.verdict == "FLAG"
    assert len(result.sections_missing) == len([
        "Product Overview",
        "Functional Requirements",
        "Non-Functional Requirements",
        "Technical Architecture",
        "Acceptance Criteria",
        "Milestones",
        "Risks",
    ])


def test_case_insensitive_section_matching():
    """Section matching is case-insensitive."""
    prd = """# PRODUCT OVERVIEW
Great product.

# functional requirements
- Login. Acceptance criteria: should work.

# NON-FUNCTIONAL REQUIREMENTS
- Fast.

# technical architecture
FastAPI.

# ACCEPTANCE CRITERIA
All work.

# milestones
4 weeks.

# risks
Scope creep.

# security
OAuth2.
"""
    result = scan_prd(prd)
    assert len(result.sections_missing) == 0


def test_security_keywords_varied():
    """Various security-related keywords are detected."""
    base_prd = """# Product Overview
Great.

# Functional Requirements
- Login. Acceptance criteria: should work.

# Non-Functional Requirements
- Fast.

# Technical Architecture
FastAPI.

# Acceptance Criteria
All work.

# Milestones
4 weeks.

# Risks
Scope creep.
"""
    # Test "authentication"
    prd = base_prd + "\n# Authentication\nOAuth2."
    result = scan_prd(prd)
    security_findings = [f for f in result.findings if "security" in f.section.lower()]
    assert len(security_findings) == 0
    
    # Test "data handling"
    prd = base_prd + "\n# Data Handling\nWe handle data carefully."
    result = scan_prd(prd)
    security_findings = [f for f in result.findings if "security" in f.section.lower()]
    assert len(security_findings) == 0
    
    # Test "privacy"
    prd = base_prd + "\n# Privacy\nWe respect privacy."
    result = scan_prd(prd)
    security_findings = [f for f in result.findings if "security" in f.section.lower()]
    assert len(security_findings) == 0
