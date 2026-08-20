"""Steering inbox + URL fetch + resume logic tests (no LLM calls)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.steering import SteeringInbox  # noqa: E402
from src.url_fetch import validate_url, fetch_urls  # noqa: E402
from src.agents.pipeline import _parse_verdict, _overall_average  # noqa: E402


def test_steering_drain():
    box = SteeringInbox()
    box.add_steering("focus on EU")
    box.add_urls(["https://example.com"])
    box.add_idea("new idea")
    assert box.drain_steering() == ["focus on EU"]
    assert box.drain_steering() == []  # drained
    assert box.drain_urls() == ["https://example.com"]
    assert box.drain_ideas() == ["new idea"]


def test_steering_ignores_blank():
    box = SteeringInbox()
    box.add_steering("   ")
    box.add_urls([])
    assert box.drain_steering() == []
    assert box.drain_urls() == []


def test_validate_url():
    assert validate_url("https://example.com/page") is True
    assert validate_url("http://example.com") is True
    assert validate_url("ftp://example.com") is False
    assert validate_url("not-a-url") is False
    assert validate_url("javascript:alert(1)") is False


def test_fetch_urls_skips_invalid():
    out = fetch_urls(["not-a-url", "ftp://x", "javascript:alert(1)"])
    assert out == ""


def test_parse_verdict_priorities():
    # PROCEED keyword wins over PARK
    assert _parse_verdict("PROCEED")["verdict"] == "PROCEED"
    assert _parse_verdict("something PRUNE here")["verdict"] == "PRUNE"


def test_overall_average():
    v = {"scores": {"novelty": {"score": 8}, "feasibility": {"score": 7}, "market_fit": {"score": 6}}}
    assert abs(_overall_average(v) - 7.0) < 0.01
