"""Dashboard authentication  -- S6 + A5 hardening (multiuser security review).

Google Identity Services verifies the JWT with google-auth; access is gated by
the ALLOWED_EMAILS allowlist (prototype)  -- replaced by SIGNUP_CLOSED in the
multi-user phase.

Sessions are SERVER-SIDE (src/sessions.py): the cookie carries an opaque random
token, only its sha256 hash is stored, every login rotates the token, and
logout revokes the row. No stateless signed cookies  -- those cannot be revoked.
"""
from __future__ import annotations

from functools import wraps
from typing import Callable

from fastapi import HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from . import config
from .sessions import session_store


def create_session_token(email: str, name: str, picture: str, user_id: str = "") -> str:
    """Issue a fresh server-side session; returns the raw cookie token.

    Rotation (G7): every call creates a NEW session  -- a token presented at
    login is never reused, which prevents session fixation.
    """
    return session_store.create(email, name=name, picture=picture, user_id=user_id)


def verify_session_token(token: str) -> dict | None:
    """Validate against the server-side store (revocation + sliding expiry)."""
    return session_store.get(token)


def revoke_session(token: str) -> None:
    session_store.revoke(token)


def get_current_user(request: Request) -> dict:
    """FastAPI dependency: returns the logged-in user or raises 401.

    When config.NO_AUTH is set (prototype phase), returns a synthetic local
    user so protected routes pass through without any login.
    """
    if config.NO_AUTH:
        return {"user_id": "local", "email": "local", "name": "Local", "picture": ""}
    token = request.cookies.get("vb_session")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    data = verify_session_token(token)
    if not data:
        raise HTTPException(status_code=401, detail="Session expired")
    return data


def login_required(handler: Callable) -> Callable:
    """Decorator that 401s unauthenticated requests (FastAPI-style)."""
    @wraps(handler)
    async def wrapper(request: Request, *args, **kwargs):
        get_current_user(request)  # raises 401 if not authed
        return await handler(request, *args, **kwargs)
    return wrapper
