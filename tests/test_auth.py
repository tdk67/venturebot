"""Tests for auth module — auth.py."""
import pytest
from fastapi import HTTPException

from venturebot import auth, config


def test_verify_google_credential_rejects_empty():
    """Empty credential should be rejected."""
    with pytest.raises(HTTPException) as exc_info:
        auth.verify_google_credential("")
    assert exc_info.value.status_code == 400


def test_verify_google_credential_rejects_garbage():
    """Garbage credential should be rejected (401 if GOOGLE_CLIENT_ID set, 500 if not)."""
    with pytest.raises(HTTPException) as exc_info:
        auth.verify_google_credential("not-a-real-jwt-token")
    # Will be 401 if GOOGLE_CLIENT_ID is configured, 500 if not
    assert exc_info.value.status_code in (400, 401, 500)


def test_verify_google_credential_rejects_expired_format():
    """Malformed JWT should be rejected."""
    with pytest.raises(HTTPException) as exc_info:
        auth.verify_google_credential("eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJ0ZXN0In0.signature")
    # Will be 401 if GOOGLE_CLIENT_ID is configured, 500 if not
    assert exc_info.value.status_code in (400, 401, 500)


def test_create_session_token_returns_string():
    """create_session_token should return a non-empty string."""
    token = auth.create_session_token("test@example.com", "Test User", "pic.jpg")
    assert isinstance(token, str)
    assert len(token) > 10


def test_get_current_user_rejects_missing_cookie():
    """Request without session cookie should raise 401."""
    from unittest.mock import Mock
    
    request = Mock()
    request.cookies = {}
    
    with pytest.raises(HTTPException) as exc_info:
        auth.get_current_user(request)
    assert exc_info.value.status_code == 401


def test_get_current_user_rejects_invalid_token():
    """Request with invalid session token should raise 401."""
    from unittest.mock import Mock
    
    request = Mock()
    request.cookies = {"vb_session": "invalid-token"}
    
    with pytest.raises(HTTPException) as exc_info:
        auth.get_current_user(request)
    assert exc_info.value.status_code == 401


def test_session_token_roundtrip():
    """Token created by create_session_token should be readable."""
    email = "test@example.com"
    name = "Test User"
    picture = "https://example.com/pic.jpg"
    
    token = auth.create_session_token(email, name, picture)
    
    # Decode the token
    decoded = auth.verify_session_token(token)
    assert decoded is not None
    assert decoded["email"] == email
    assert decoded["name"] == name
    assert decoded["picture"] == picture


def test_verify_session_token_rejects_garbage():
    """verify_session_token should return None for invalid tokens."""
    assert auth.verify_session_token("not-a-token") is None
    assert auth.verify_session_token("") is None
    assert auth.verify_session_token("a.b.c") is None
