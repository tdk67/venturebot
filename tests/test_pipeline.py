"""Non-live tests for Phase 1 pipeline logic (no LLM calls)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.pipeline import _parse_verdict, _overall_average  # noqa: E402


def test_parse_verdict_json():
    text = '{"scores": {"novelty": {"score": 8}}, "verdict": "PROCEED"}'
    assert _parse_verdict(text)["verdict"] == "PROCEED"


def test_parse_verdict_prose():
    assert _parse_verdict("The verdict is PRUNE because...")["verdict"] == "PRUNE"


def test_parse_verdict_rejects_invalid_json_keyword():
    # JSON present but missing a valid verdict -> keyword search over prose
    assert _parse_verdict('{"scores": {"novelty": {"score": 3}}} PARK')["verdict"] == "PARK"


def test_parse_verdict_empty_fails_loud():
    import pytest
    with pytest.raises(ValueError):
        _parse_verdict("")


def test_parse_verdict_uninterpretable_fails_loud():
    import pytest
    with pytest.raises(ValueError):
        _parse_verdict("The judge discussed the weather and nothing else.")


def test_overall_average():
    v = {"scores": {"novelty": {"score": 8}, "feasibility": {"score": 7}, "market_fit": {"score": 6}}}
    assert abs(_overall_average(v) - 7.0) < 0.01


def test_overall_average_none():
    assert _overall_average({}) is None
    assert _overall_average({"scores": {}}) is None


def test_agents_have_correct_tool_separation():
    from src.agents.agents import ALL_AGENTS
    assert len(ALL_AGENTS["advocate"].tools) == 0  # blind
    assert len(ALL_AGENTS["critic"].tools) == 1  # has search
    assert len(ALL_AGENTS["researcher"].tools) == 2  # search + clarify
    assert len(ALL_AGENTS["judge"].tools) == 0
    # S10 Security Auditor: no tools, structured output schema
    assert len(ALL_AGENTS["auditor"].tools) == 0
    assert ALL_AGENTS["auditor"].output_schema is not None
