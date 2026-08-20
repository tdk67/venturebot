"""Dashboard authentication — S6. Google OAuth single sign-on (FastAPI port).

Ports the working pattern from /root/diary-app/app/auth.py:
  - Google Identity Services (GIS) verifies the JWT with google-auth
  - ALLOWED_EMAILS allowlist gates access
  - @login_required protects every route

Sessions are signed cookies (itsdangerous) — no server-side session store
needed for a single-user dashboard.
"""
from __future__ import annotations

import os
from functools import wraps
from typing import Callable

from fastapi import HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import config

# Session signing secret: use the explicit env var if set; otherwise derive a
# stable per-install secret from the Google API key (not stored in the repo).
_SESSION_SECRET = os.environ.get("VENTUREBOT_SESSION_SECRET", "").strip()
if not _SESSION_SECRET:
    _SESSION_SECRET = config.google_api_key()  # stable, secret, not committed
_signer = URLSafeTimedSerializer(_SESSION_SECRET, salt="venturebot-session")


def create_session_token(email: str, name: str, picture: str) -> str:
    return _signer.dumps({"email": email, "name": name, "picture": picture})


def verify_session_token(token: str) -> dict | None:
    try:
        data = _signer.loads(token, max_age=30 * 24 * 3600)
        return data
    except (BadSignature, SignatureExpired):
        return None


def verify_google_credential(credential: str) -> dict:
    """Verify a Google ID token and enforce the email allowlist.

    Returns {email, name, picture} on success. Raises HTTPException on failure.
    """
    if not config.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=500,
            detail="GOOGLE_CLIENT_ID is not configured. Set it in .env.",
        )
    if not credential:
        raise HTTPException(status_code=400, detail="Missing credential")

    try:
        idinfo = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            config.GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=10,
        )
    except ValueError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

    email = idinfo.get("email", "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="No email in token")

    if config.ALLOWED_EMAILS and email not in config.ALLOWED_EMAILS:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: {email} is not authorized.",
        )

    return {
        "email": email,
        "name": idinfo.get("name", ""),
        "picture": idinfo.get("picture", ""),
    }


def get_current_user(request: Request) -> dict:
    """FastAPI dependency: returns the logged-in user or raises 401.

    When config.NO_AUTH is set (prototype phase), returns a synthetic local
    user so protected routes pass through without any login.
    """
    if config.NO_AUTH:
        return {"email": "local", "name": "Local", "picture": ""}
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
