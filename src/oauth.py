"""Google OAuth  -- authorization code flow with PKCE (A6 / G6 of the review).

Replaces the GIS inline ID-token flow. Per OAuth 2.1 / RFC 8252 / Google
guidance:
  - authorization CODE flow; the client secret stays server-side;
  - PKCE (S256) on every authorization request;
  - `state` (CSRF) + `nonce` (replay) generated per login, validated at
    callback, single-use, 10-minute TTL;
  - id_token verified (signature, aud, exp, nonce) before a session is minted.

Transient login attempts are held in-process (single-node deployment); they
die with the process, which is safe  -- the user just starts over.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import urllib.request

from fastapi import HTTPException

from . import config

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_STATE_TTL = 600  # 10 minutes to complete login

# state -> {"nonce", "code_verifier", "created_at"}
_pending: dict[str, dict] = {}


def _cleanup() -> None:
    now = time.time()
    for k in [k for k, v in _pending.items() if now - v["created_at"] > _STATE_TTL]:
        _pending.pop(k, None)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def begin_login(base_url: str) -> str:
    """Build the Google authorization URL; registers state/nonce/PKCE."""
    _cleanup()
    if not config.GOOGLE_CLIENT_ID:
        raise HTTPException(503, "Google login is not configured (GOOGLE_CLIENT_ID missing)")
    if not config.GOOGLE_CLIENT_SECRET:
        raise HTTPException(503, "Google login is not configured (GOOGLE_CLIENT_SECRET missing)")

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    code_verifier = _b64url(os.urandom(48))  # 43-128 chars per RFC 7636
    _pending[state] = {
        "nonce": nonce,
        "code_verifier": code_verifier,
        "created_at": time.time(),
    }

    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": f"{base_url}/api/auth/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": _b64url(hashlib.sha256(code_verifier.encode()).digest()),
        "code_challenge_method": "S256",
        "access_type": "online",  # no refresh token  -- we only need identity
        "prompt": "select_account",
    }
    return f"{_AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"


def _pop_pending(state: str) -> dict:
    _cleanup()
    entry = _pending.pop(state, None)
    if entry is None:
        # Unknown, expired, or already-used state  -> possible CSRF/replay.
        raise HTTPException(400, "Invalid or expired login state  -- start again")
    return entry


def exchange_code(code: str, state: str, base_url: str) -> dict:
    """Exchange the authorization code; verify id_token; return identity.

    Returns {sub, email, name, picture}. Raises HTTPException on any failure.
    """
    entry = _pop_pending(state)

    if not code:
        raise HTTPException(400, "Missing authorization code")
    data = urllib.parse.urlencode(
        {
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "code": code,
            "code_verifier": entry["code_verifier"],
            "grant_type": "authorization_code",
            "redirect_uri": f"{base_url}/api/auth/callback",
        }
    ).encode()
    req = urllib.request.Request(_TOKEN_ENDPOINT, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            tokens = json.loads(resp.read())
    except Exception as e:
        raise HTTPException(502, f"Token exchange failed: {e}")

    id_token_value = tokens.get("id_token", "")
    if not id_token_value:
        raise HTTPException(502, "Token response missing id_token")

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token as google_id_token

        idinfo = google_id_token.verify_oauth2_token(
            id_token_value,
            google_requests.Request(),
            config.GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=10,
        )
    except ValueError as e:
        raise HTTPException(401, f"Invalid id_token: {e}")

    # Nonce check (G6): binds the id_token to THIS login attempt.
    if idinfo.get("nonce") != entry["nonce"]:
        raise HTTPException(401, "Nonce mismatch  -- possible replay")

    email = (idinfo.get("email") or "").strip().lower()
    if not email or not idinfo.get("email_verified", False):
        raise HTTPException(403, "Google account has no verified email")

    return {
        "sub": idinfo.get("sub", ""),
        "email": email,
        "name": idinfo.get("name", ""),
        "picture": idinfo.get("picture", ""),
    }

