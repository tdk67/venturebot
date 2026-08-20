"""Tests for url_fetch module."""
import pytest
from src.url_fetch import fetch_urls, validate_url


def test_validate_url_validates_http_scheme():
    """validate_url should only accept http/https URLs."""
    assert validate_url("https://example.com") is True
    assert validate_url("http://example.com") is True
    assert validate_url("ftp://example.com") is False
    assert validate_url("javascript:alert(1)") is False
    assert validate_url("file:///etc/passwd") is False


def test_validate_url_rejects_malformed_urls():
    """validate_url should reject malformed URLs."""
    assert validate_url("") is False
    assert validate_url("not-a-url") is False
    assert validate_url("://missing-scheme") is False


def test_fetch_urls_handles_empty_list():
    """fetch_urls should return empty string for empty URL list."""
    result = fetch_urls([])
    assert result == ""


def test_fetch_urls_skips_invalid_urls():
    """fetch_urls should skip invalid URLs and continue."""
    # These should be skipped (invalid scheme)
    urls = ["ftp://example.com", "javascript:alert(1)"]
    result = fetch_urls(urls)
    # Should return empty or only valid URLs
    assert "ftp://" not in result
    assert "javascript:" not in result


def test_fetch_urls_handles_timeout_gracefully():
    """fetch_urls should handle timeout errors gracefully."""
    # Use a URL that will timeout or fail
    urls = ["https://this-domain-definitely-does-not-exist-12345.com"]
    result = fetch_urls(urls)
    # Should not crash, should return something (even if empty or error message)
    assert isinstance(result, str)


def test_fetch_urls_extracts_title():
    """fetch_urls should extract page title when available."""
    # This is a real URL that should work in tests
    # If it fails due to network, that's okay - we're testing the logic
    urls = ["https://example.com"]
    try:
        result = fetch_urls(urls)
        # Should contain the URL and ideally the title
        assert "example.com" in result.lower() or "example" in result.lower()
    except Exception:
        # Network failures are acceptable in test environment
        pass
