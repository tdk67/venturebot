# VentureBot — Bearer Token Architecture (Complete)

**Date:** 2026-08-26
**Status:** Implementation-ready design

---

## 1. The Problem & The Solution

### Current State
- Google OAuth code flow + PKCE + server-side sessions — ~500 lines, broken at `idea-lint.my`
- `VENTUREBOT_NO_AUTH=1` bypasses everything → no per-user isolation at all
- All data is global (single `state.json`, shared `_RUNS` dict, single `_SSE_CLIENTS` set)
- The multi-user design docs describe a full local-first/IndexedDB architecture, but **none of the per-user scoping code exists yet**

### What We Actually Need
1. **Per-user isolation** — Alice must never see Bob's ideas, debates, or results
2. **Simple auth** — a token, not a 500-line OAuth dance
3. **SSE streaming still works** — the debate feed is the core UX
4. **BYOK support** — users bring their own API keys to avoid shared costs
5. **GCP deployment** — hackathon requirement, already on Firebase + Cloud Run
6. **Client stores ideas** — server is compute-only, client is source of truth (per MULTI_USER_DESIGN.md)

---

## 2. Bearer Token Auth — The Full Flow

### 2.1 Token Generation (operator-side, pre-deploy)

```bash
# Generate tokens for each user
python3 <<'EOF'
import secrets, hashlib, json

users = {
    "alice": "Alice (Judge 1)",
    "bob":   "Bob (Judge 2)",
    "carol": "Carol (Mentor)",
}

tokens = {}
for uid, name in users.items():
    raw = "vb_" + secrets.token_urlsafe(32)
    h = hashlib.sha256(raw.encode()).hexdigest()
    tokens[uid] = {"raw": raw, "hash": h, "display_name": name}
    print(f"{name} ({uid}):")
    print(f"  Token:  {raw}")
    print(f"  Link:   https://idea-lint.my/?token={raw}")
    print()

# Save the hashes for Secret Manager
hashes = {uid: {"hash": t["hash"], "display_name": t["display_name"]} for uid, t in tokens.items()}
with open("user_tokens.json", "w") as f:
    json.dump(hashes, f, indent=2)
print("Saved user_tokens.json for Secret Manager")
EOF
```

This produces:
```
Alice (Judge 1) (alice):
  Token:  vb_Abc123Def456Ghi789Jkl012Mno345Pqr678Stu901Vwx234
  Link:   https://idea-lint.my/?token=vb_Abc123Def456Ghi789Jkl012Mno345Pqr678Stu901Vwx234
```

### 2.2 Token Delivery

**For hackathon:** share the `?token=...` link via Signal/WhatsApp. User clicks it once,
the SPA reads the query param, stores it in `localStorage`, and redirects to `/`.

**For production:** tokens are emailed or shown in a dashboard. The `?token=` pattern
is convenient but the token appears in browser history. For hackathon, this is fine.

### 2.3 How the Token Reaches the Backend

**For regular API calls** (`fetch()`): the SPA sends an `Authorization` header:

```javascript
// /static/app.js — wrapper around fetch
const TOKEN = () => localStorage.getItem('vb_token') || '';

async function api(method, path, body) {
    const headers = {
        'Content-Type': 'application/json',
    };
    if (TOKEN()) {
        headers['Authorization'] = 'Bearer ' + TOKEN();
    }
    const res = await fetch(path, {
        method,
        headers,
        body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401) {
        localStorage.removeItem('vb_token');
        showTokenEntry();
        throw new Error('Unauthorized');
    }
    return res;
}
```

**For SSE** (`EventSource`): **this is the tricky part.** `EventSource` doesn't support
custom headers. Three options:

| Approach | Works? | Complexity |
|----------|--------|------------|
| **Query param** `EventSource('/api/events?token=...')` | ✅ Yes | Adds token to URL (appears in server logs) |
| **Cookie-based** (set `vb_token` cookie from JS, read in middleware) | ✅ Yes | Cookie must be SameSite=None; needs secure flag; mixed with old cookie logic |
| **fetch() streaming** (abandon EventSource, parse SSE manually) | ✅ Yes | Need to rewrite ~50 lines of SSE handling |
| **EventSource with session** (POST `/api/events/auth` with token → gets a short-lived session ID → EventSource with session cookie) | ⚠️ Complex | Adds 2 round trips |

**Decision: Query param for SSE.** The token already appears in the `?token=` link
that the user first opens. Adding it to the SSE URL is not a new exposure. Server logs
are already redacting secrets (the BYOK key redaction pipeline exists). A 256-bit
random token in a URL is no worse than a session cookie — both are bearer credentials.

```javascript
function connectSSE() {
    const t = TOKEN();
    const url = t ? '/api/events?token=' + encodeURIComponent(t) : '/api/events';
    const es = new EventSource(url);
    // ... rest unchanged
}
```

Server middleware extracts the token from `Authorization` header OR `?token=` query param:

```python
def _extract_token(request: Request) -> str | None:
    # 1. Authorization header (for fetch() calls)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    # 2. Query param (for SSE EventSource)
    token = request.query_params.get("token", "")
    if token.startswith("vb_"):
        return token
    return None
```

### 2.4 Backend Middleware (~60 lines total)

```python
# src/auth.py — replaces the entire file
"""Bearer token authentication — per-user identity without OAuth complexity."""

from __future__ import annotations

import hashlib
import json
import os
from functools import wraps

from fastapi import HTTPException, Request

# ── User store (in-memory, loaded from env on startup) ──────────────────

_users: dict[str, dict] = {}  # token_hash → {user_id, display_name}

def _load_users():
    """Load user token hashes from VENTUREBOT_USER_TOKENS env var (JSON map)."""
    raw = os.environ.get("VENTUREBOT_USER_TOKENS", "{}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    for uid, info in data.items():
        if isinstance(info, str):
            info = {"hash": info, "display_name": uid}
        _users[info["hash"]] = {"user_id": uid, "display_name": info.get("display_name", uid)}

_load_users()

# ── Token verification ──────────────────────────────────────────────────

def verify_token(token: str) -> dict | None:
    """Return {user_id, display_name} or None."""
    if not token or not token.startswith("vb_"):
        return None
    th = hashlib.sha256(token.encode()).hexdigest()
    return _users.get(th)

# ── Public paths (no auth required) ─────────────────────────────────────

_PUBLIC_PREFIXES = ("/api/auth/me",)  # me returns user info or {authenticated: false}
_PUBLIC_EXACT = ("/", "/api/health")

def _is_public(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    return path.startswith(_PUBLIC_PREFIXES)

# ── FastAPI dependency + middleware ──────────────────────────────────────

def get_current_user(request: Request) -> dict:
    """FastAPI dependency: returns {user_id, display_name} or raises 401."""
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    user = verify_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return user

# Middleware: inject request.state.user for all protected routes
async def bearer_middleware(request: Request, call_next):
    if request.url.path.startswith("/api/") and not _is_public(request.url.path):
        token = _extract_token(request)
        if not token:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Missing Bearer token"}, status_code=401)
        user = verify_token(token)
        if not user:
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Invalid or revoked token"}, status_code=401)
        request.state.user = user
    return await call_next(request)
```

That's the entire auth module. ~60 lines vs the current ~500 (oauth.py + sessions.py + auth.py).

### 2.5 `/api/auth/me` Endpoint

```python
@app.get("/api/auth/me")
async def auth_me(request: Request):
    token = _extract_token(request)
    if not token:
        return {"authenticated": False, "email": None}
    user = verify_token(token)
    if not user:
        return {"authenticated": False, "email": None}
    return {
        "authenticated": True,
        "email": user["display_name"],
        "user_id": user["user_id"],
        "name": user["display_name"],
        "picture": "",
    }
```

The SPA calls this on load. If it gets `{authenticated: true}`, it shows the app.
If `{authenticated: false}`, it shows the token entry screen.

---

## 3. Frontend Changes

### 3.1 Token Entry Screen (replaces Google login gate)

```html
<!-- Replaces the current Google OAuth login screen -->
<div id="token-screen" class="min-h-screen flex items-center justify-center">
  <div class="bg-slate-950 p-10 rounded-2xl border border-slate-800 text-center max-w-md">
    <h1 class="text-2xl font-black text-white mb-2">VentureBot <span class="text-blue-500">Command Center</span></h1>
    <p class="text-slate-400 text-sm mb-6">Enter your access token to continue.</p>

    <!-- If ?token=... is in URL, pre-fill and auto-submit -->
    <input id="token-input" type="text"
           placeholder="Paste your VentureBot access token"
           class="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-3 text-sm text-slate-200 focus:outline-none focus:border-blue-500 mb-4 font-mono">

    <button id="btn-token"
            class="w-full px-6 py-3 bg-blue-600 hover:bg-blue-500 text-white text-sm font-bold rounded-lg">
      Enter VentureBot
    </button>

    <p id="token-error" class="hidden text-red-400 text-xs mt-4"></p>
  </div>
</div>
```

### 3.2 App Startup Flow

```javascript
// On page load
async function init() {
    // 1. Check URL for ?token=
    const params = new URLSearchParams(location.search);
    const urlToken = params.get('token');
    if (urlToken) {
        localStorage.setItem('vb_token', urlToken);
        // Clean URL (remove token from address bar / history)
        history.replaceState(null, '', '/');
    }

    // 2. Check localStorage for existing token
    const stored = localStorage.getItem('vb_token');
    if (!stored) {
        showTokenEntry();  // show token input screen
        return;
    }

    // 3. Validate token against server
    try {
        const res = await fetch('/api/auth/me', {
            headers: { 'Authorization': 'Bearer ' + stored }
        });
        const data = await res.json();
        if (data.authenticated) {
            showApp(data);  // show main app
        } else {
            localStorage.removeItem('vb_token');
            showTokenEntry();
        }
    } catch {
        showTokenEntry();
    }
}

// Token entry handler
document.getElementById('btn-token').onclick = async () => {
    const token = document.getElementById('token-input').value.trim();
    if (!token) return;

    const res = await fetch('/api/auth/me', {
        headers: { 'Authorization': 'Bearer ' + token }
    });
    const data = await res.json();
    if (data.authenticated) {
        localStorage.setItem('vb_token', token);
        document.getElementById('token-screen').classList.add('hidden');
        showApp(data);
    } else {
        document.getElementById('token-error').textContent = 'Invalid token. Please try again.';
        document.getElementById('token-error').classList.remove('hidden');
    }
};
```

---

## 4. Per-User Isolation — Implementation

### 4.1 Route Ownership Checks

Every route that accesses user-specific data must verify `request.state.user.user_id`:

```python
# Pattern used in every route handler:
user = request.state.user
user_id = user["user_id"]

# Ideas:
@app.get("/api/ideas")
async def api_ideas(request: Request, page: int = 1, ...):
    user_id = request.state.user["user_id"]
    ideas = get_store().get_idea_tree(user_id=user_id)
    # ... filter/paginate as before

@app.get("/api/ideas/{idea_id}")
async def api_idea_detail(idea_id: str, request: Request):
    user_id = request.state.user["user_id"]
    idea = get_store().get_idea(idea_id, user_id=user_id)
    if not idea:
        raise HTTPException(404, "idea not found")
    # ...

# Debates:
# The _RUNS dict becomes keyed by (user_id, run_id)
# The SSE fan-out sends only to the owner
```

### 4.2 SQLite Changes

Add `user_id` to every table:

```sql
ALTER TABLE ideas ADD COLUMN user_id TEXT NOT NULL DEFAULT '';
ALTER TABLE idea_runs ADD COLUMN user_id TEXT NOT NULL DEFAULT '';
ALTER TABLE agent_lessons ADD COLUMN user_id TEXT NOT NULL DEFAULT '';
ALTER TABLE agent_techniques ADD COLUMN user_id TEXT NOT NULL DEFAULT '';

-- Every SELECT gets: WHERE user_id = ?
-- Every INSERT gets: user_id = ?
```

### 4.3 SSE Fan-Out Per User

```python
# Current (global):
_SSE_CLIENTS: set[asyncio.Queue] = set()

# New (per-user):
_SSE_CLIENTS: dict[str, set[asyncio.Queue]] = {}  # user_id → queues

async def _broadcast(event: str, data: dict, user_id: str | None = None):
    payload = _sse_format(event, data)
    if user_id:
        queues = _SSE_CLIENTS.get(user_id, set())
    else:
        # System events (no user_id) → broadcast to all
        queues = set()
        for qs in _SSE_CLIENTS.values():
            queues.update(qs)

    for q in list(queues):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass
```

### 4.4 IDOR Defense (Preventing UUID Guessing)

Layered defense:

1. **UUIDv4 run_ids** — 122 bits of entropy, unguessable
2. **Ownership check returns 404**, never 403 — attacker can't tell if a run_id exists
3. **No listing endpoint** — users only know run_ids they created
4. **Rate limiting** — throttle by token, prevent enumeration
5. **Token binding** — SSE connection is bound to `user_id` at connect time; can't be
   replayed for another user

---

## 5. Rate Limiting (~40 lines)

```python
# Simple in-memory token bucket
from collections import defaultdict
import time

_rate_buckets: dict[str, list[float]] = defaultdict(list)
_ATTEMPT_BUCKETS: dict[str, list[float]] = defaultdict(list)  # for failed auth attempts

def check_rate(token_or_ip: str, limit: int = 30, window: int = 60) -> bool:
    now = time.time()
    bucket = _rate_buckets[token_or_ip]
    bucket[:] = [t for t in bucket if t > now - window]
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True

def check_auth_rate(ip: str, limit: int = 5, window: int = 60) -> bool:
    """Rate-limit failed auth attempts per IP."""
    now = time.time()
    bucket = _ATTEMPT_BUCKETS[ip]
    bucket[:] = [t for t in bucket if t > now - window]
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True
```

Middleware: check rate on every API call, return 429 if exceeded.

---

## 6. BYOK (Bring Your Own Key)

### 6.1 Flow

```
Client stores: localStorage.setItem('vb_byok_key', 'sk-or-v1-...')

Every debate-submit:
  POST /api/run-phase1
  Authorization: Bearer vb_abc123
  X-VB-Api-Key: sk-or-v1-...     ← user's OpenRouter or Gemini key
  {idea: "..."}

Backend:
  1. Validate token → user_id
  2. Extract X-VB-Api-Key from header
  3. Validate the key (cheap check: call /models endpoint)
  4. Store key in request.state (memory only)
  5. Pass to orchestrator → sub-agents
  6. NEVER log the key (redacted middleware already exists for uvicorn access logs)
  7. Key is garbage-collected when the run ends
```

### 6.2 Key Validation

```python
import httpx

async def validate_byok_key(key: str) -> str | None:
    """Validate a BYOK key. Returns the key if valid, None otherwise.
    
    For OpenRouter keys, hits the models endpoint (cheap, no token cost).
    For Gemini keys, checks format and lets the first LLM call validate.
    For unknown keys, passes through and lets the orchestrator fail fast.
    """
    key = key.strip()
    if not key:
        return None
    
    if key.startswith("sk-or-"):
        # OpenRouter key — validate
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://openrouter.ai/api/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=5,
                )
                if resp.status_code == 200:
                    return key
        except Exception:
            pass
        return None
    
    # Gemini key or unknown — pass through, fail at first LLM call if invalid
    return key
```

### 6.3 Fallback Config

```python
# config.py
ALLOW_SERVER_KEY_FALLBACK = os.environ.get(
    "VENTUREBOT_ALLOW_SERVER_KEY_FALLBACK", "false"
).lower() in ("1", "true", "yes")

# In the route:
byok_key = request.headers.get("X-VB-Api-Key", "").strip()
if not byok_key:
    if not config.ALLOW_SERVER_KEY_FALLBACK:
        raise HTTPException(400, "BYOK key required — set X-VB-Api-Key header")
    # Fall back to server key
    byok_key = None  # orchestrator will use GOOGLE_API_KEY
```

### 6.4 Key Redaction

The existing `src/store.py` already redacts log output (`<log message, N chars>` instead
of the full text). Add an explicit check:

```python
# In the middleware or wherever headers are logged:
def _redact_headers(headers: dict) -> dict:
    """Redact sensitive headers before logging."""
    redacted = dict(headers)
    for key in ("authorization", "x-vb-api-key"):
        if key in redacted:
            redacted[key] = "[REDACTED]"
    return redacted
```

---

## 7. Files That Change

### New/Replaced:

| File | Action | Lines |
|------|--------|-------|
| `src/auth.py` | **Rewrite** — Bearer token verify, middleware, dependency | ~60 |
| `src/rate_limit.py` | **New** — Token bucket rate limiter | ~40 |
| `static/app.js` | **Edit** — Token entry screen, `api()` wrapper with auth | +50 |

### Modified:

| File | Change | Lines |
|------|--------|-------|
| `src/dashboard.py` | Remove OAuth routes, add `/api/auth/me`, per-user scoping on SSE, add `request.state.user` to all routes | -80, +60 |
| `src/config.py` | Remove `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ALLOWED_EMAILS`, `SIGNUP_CLOSED`, `COOKIE_SECURE`; add `VENTUREBOT_USER_TOKENS`, `ALLOW_SERVER_KEY_FALLBACK` | -10, +5 |
| `src/store.py` | Add `user_id` to all SQL queries, state.json → per-user | ~20 |
| `templates/index.html` | Replace Google login with token entry screen | -30, +35 |

### Deleted:

| File | Why |
|------|-----|
| `src/oauth.py` | Google OAuth code flow — replaced by Bearer token |
| `src/sessions.py` | Server-side session store — replaced by token hashes in env |

### Unchanged:

| File | Why |
|------|-----|
| `src/agents/orchestrator.py` | Runs per-user already (per-run workspace, per-run _RUNS entry) |
| `src/agents/agents.py` | Sub-agent definitions — unchanged |
| `src/llm_client.py` | LLM calls — just need to receive BYOK key |
| `src/memory/*` | Memory store — just add `user_id` filtering |
| `src/steering.py` | Steering inbox — already per-run scoped |
| `src/sandbox.py`, `src/budget.py`, `src/run_manager.py` | Unchanged |
| `src/events.py` | Unchanged (just the subscriber pattern) |

---

## 8. GCP Deployment

### Secret Manager Entries

```
GOOGLE_API_KEY          → Gemini API key (server fallback)
VENTUREBOT_USER_TOKENS  → {"alice": {"hash": "abc...", "display_name": "Alice"}, ...}
```

### Cloud Run Env Vars

```
VENTUREBOT_NO_AUTH=0
VENTUREBOT_PUBLIC_BASE_URL=https://idea-lint.my
VENTUREBOT_ALLOW_SERVER_KEY_FALLBACK=true   # for prototype; false for production
```

### Firebase Hosting

Unchanged — the `firebase.json` rewrite to Cloud Run passes all headers through.
No change needed for `Authorization` or `X-VB-Api-Key` headers.

---

## 9. Security Analysis

### What Could Go Wrong?

| Threat | Likelihood | Impact | Mitigation |
|--------|-----------|--------|-----------|
| Token in URL history (`?token=`) | Medium | Low (hackathon, trusted users) | `history.replaceState` cleans it immediately; token in SSE query param is in server logs but logs are redacted |
| Token leaked via browser devtools | Low | Medium (local machine only) | Tokens are per-user, revocable, and scoped to ideas only |
| XSS steals token from localStorage | Low | Medium | Strict CSP: `script-src 'self'` — no third-party scripts can read localStorage |
| UUID guessing | Very low | Very low (122-bit entropy) | Ownership check + 404 masking + rate limiting |
| BYOK key in server memory | Low (transient) | Medium | NEVER logged, NEVER persisted, garbage-collected at run end |
| Token brute-force | Very low (256-bit) | None | Rate limiting on auth (5 attempts/minute per IP) |
| Server compromise reads all tokens | Low | High | Tokens are in env (Secret Manager), not in DB. DB has token hashes only. |

### What This Does NOT Solve (explicitly accepted):

1. **Server process compromise** — if Cloud Run is breached, the attacker can read
   in-flight debates and BYOK keys in memory. This is the same as Google OAuth.
2. **Insider threat** — the operator can see all tokens and provision new ones.
   This is inherent in any server-side auth.
3. **Token sharing** — Alice could give her token to Bob. Mitigated by: this is a
   prototype with trusted users; tokens can be revoked individually.

---

## 10. Migration Plan: Bearer Token → Google SSO

When (if) you want Google SSO back:

1. Add a `google_sub` column to the `users` table (nullable, default NULL)
2. Re-enable the OAuth flow (`oauth.py` + `sessions.py` are saved in git history)
3. On first Google login: create a user row with `google_sub`, link to existing
   `user_id` if the operator pre-mapped it
4. On subsequent logins: resolve `google_sub` → `user_id`, use the same per-user
   scoping code
5. The per-user isolation code (user_id on every row, ownership checks, SSE per-user,
   rate limiting) **doesn't change at all** — it works identically whether `user_id`
   comes from a Bearer token or a Google account.

The Bearer token approach is just a simpler identity provider. The rest of the
multi-user architecture is the same.

---

## 11. Implementation Checklist

### Phase 0 — Auth Migration (2-3 hours)

- [ ] Rewrite `src/auth.py` (Bearer middleware + `verify_token` + `get_current_user`)
- [ ] Add `src/rate_limit.py` (token bucket)
- [ ] Update `src/dashboard.py`:
  - [ ] Remove `/api/auth/login`, `/api/auth/callback`, `/api/auth/logout`, `/api/auth/client-id`
  - [ ] Rewrite `/api/auth/me` to check Bearer token
  - [ ] Add `_extract_token()` utility (checks Authorization header + `?token=` query param)
  - [ ] Wire `bearer_middleware` into the app
  - [ ] Add `request.state.user` to all route handlers
- [ ] Update `src/config.py`:
  - [ ] Remove `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ALLOWED_EMAILS`, `SIGNUP_CLOSED`, `COOKIE_SECURE`
  - [ ] Add `VENTUREBOT_USER_TOKENS`, `ALLOW_SERVER_KEY_FALLBACK`
- [ ] Delete `src/oauth.py`, `src/sessions.py`
- [ ] Update `templates/index.html`:
  - [ ] Replace Google login screen with token entry screen
  - [ ] Add `?token=` URL handling
- [ ] Update `static/app.js`:
  - [ ] Add `api()` wrapper that sends Authorization header
  - [ ] Add `TOKEN()` accessor from localStorage
  - [ ] Update `connectSSE()` to pass `?token=` query param
  - [ ] Update `init()` to check localStorage token + validate via `/api/auth/me`
- [ ] Remove `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` from `.env`

### Phase 1 — Per-User Isolation (2-3 hours)

- [ ] Add `user_id` column to `ideas`, `idea_runs`, `agent_lessons`, `agent_techniques` tables
- [ ] Update `get_store()` methods to filter by `user_id`
- [ ] Update `dashboard.py` routes to verify ownership (404 on mismatch)
- [ ] Make `_RUNS` dict keyed by `(user_id, run_id)`
- [ ] Make `_SSE_CLIENTS` per-user (dict keyed by `user_id`)
- [ ] Update `_broadcast()` to scope by `user_id`
- [ ] Make `state.json` per-user (or remove it — state is per-run in orchestrator)

### Phase 2 — BYOK (1 hour)

- [ ] Add `X-VB-Api-Key` header extraction in `api_run_phase1`
- [ ] Add key validation (`validate_byok_key()`)
- [ ] Pass key to orchestrator → sub-agents
- [ ] Add key redaction in logs
- [ ] Add `ALLOW_SERVER_KEY_FALLBACK` config

### Phase 3 — Security Hardening (1 hour)

- [ ] Add `_RATE_LIMIT` middleware on all API routes
- [ ] Add auth rate limiting (5/min per IP on `/api/auth/me`)
- [ ] Add two-user IDOR test (`tests/test_per_user_isolation.py`)
- [ ] Add canary key leak test
- [ ] Add BYOK key redaction test

### Phase 4 — Deploy (30 min)

- [ ] Generate tokens → `user_tokens.json`
- [ ] Upload to Secret Manager: `VENTUREBOT_USER_TOKENS`, `GOOGLE_API_KEY`
- [ ] Update Cloud Run env vars
- [ ] Redeploy
- [ ] Test with two different tokens on two different browsers