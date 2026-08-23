"""VentureBot configuration.

All tunables read from environment (via .env loaded by dotenv). No hardcoded
secrets. The `budget` module exposes live state; static values live here.

Provider credential resolution:
  - GOOGLE_API_KEY  -> Google AI Studio (Phase 1 ADK Gemini)
  - OPENROUTER_API_KEY -> OpenRouter (Phase 2); falls back to ~/.pi/agent/auth.json
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ── Paths ──────────────────────────────────────────────────────────────
WORKSPACE_DIR = Path(os.environ.get("VENTUREBOT_WORKSPACE", BASE_DIR / "workspace"))
STATE_FILE = Path(os.environ.get("VENTUREBOT_STATE", BASE_DIR / "state.json"))
DATA_DIR = Path(os.environ.get("VENTUREBOT_DATA", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("VENTUREBOT_DB", DATA_DIR / "venturebot.db"))
SANDBOX_DIR = Path(os.environ.get("VENTUREBOT_SANDBOX", BASE_DIR / "sandbox"))
CHECKPOINT_DIR = Path(os.environ.get("VENTUREBOT_CHECKPOINT_DIR", DATA_DIR / "checkpoints"))
ARCHIVE_DIR = Path(os.environ.get("VENTUREBOT_ARCHIVE_DIR", DATA_DIR / "archives"))

# ── Loop budget ────────────────────────────────────────────────────────
MAX_ITERATIONS = int(os.environ.get("VENTUREBOT_MAX_ITERATIONS", "5"))
LLM_TIMEOUT = int(os.environ.get("VENTUREBOT_LLM_TIMEOUT", "120"))
MAX_TOKENS = int(os.environ.get("VENTUREBOT_MAX_TOKENS", "4096"))
RUN_DEADLINE_SECONDS = int(os.environ.get("VENTUREBOT_RUN_DEADLINE", "900"))  # 15 min

# ── Orchestrator loop budget ────────────────────────────────────────────
# Maximum turns the orchestrator may take before it must stop and present results.
ORCHESTRATOR_MAX_TURNS = int(os.environ.get("VENTUREBOT_ORCHESTRATOR_MAX_TURNS", "10"))
# Maximum sub-agent calls per orchestrator turn.
ORCHESTRATOR_MAX_TOOL_CALLS = int(os.environ.get("VENTUREBOT_ORCHESTRATOR_MAX_TOOL_CALLS", "50"))
# Quality gate: if the orchestrator has a PRD + verdict and hasn't made progress
# for this many consecutive turns, it stops.
ORCHESTRATOR_STALL_TURNS = int(os.environ.get("VENTUREBOT_ORCHESTRATOR_STALL_TURNS", "3"))

# ── Budget (enforced in budget.py; configurable + human-raisable) ──────
DAILY_BUDGET_LIMIT_USD = float(
    os.environ.get("VENTUREBOT_DAILY_BUDGET_LIMIT", "20.00")
)

# ── Models: Phase 1 (Gemini / ADK) ─────────────────────────────────────
MODEL_RESEARCHER = os.environ.get("VENTUREBOT_MODEL_RESEARCHER", "gemini-3.7-flash")
MODEL_ADVOCATE = os.environ.get("VENTUREBOT_MODEL_ADVOCATE", "gemini-3.7-flash")
MODEL_CRITIC = os.environ.get("VENTUREBOT_MODEL_CRITIC", "gemini-3.1-pro-preview")
MODEL_JUDGE = os.environ.get("VENTUREBOT_MODEL_JUDGE", "gemini-3.1-pro-preview")
MODEL_PRD_WRITER = os.environ.get("VENTUREBOT_MODEL_PRD_WRITER", "gemini-3.1-pro-preview")
MODEL_AUDITOR = os.environ.get("VENTUREBOT_MODEL_AUDITOR", "gemini-3.1-pro-preview")
MODEL_CREATIVE = os.environ.get("VENTUREBOT_MODEL_CREATIVE", "gemini-3.7-flash")

# ── Model: Orchestrator ─────────────────────────────────────────────────
MODEL_ORCHESTRATOR = os.environ.get("VENTUREBOT_MODEL_ORCHESTRATOR", "gemini-3.1-pro-preview")

# ── Temperatures (higher = more exploratory) ───────────────────────────
# The Creative head runs hot on purpose: it is the divergent thinker that the
# precise Advocate/Critic/Judge cannot be. Its output is always re-checked by
# the evidence-bound Critic before it can influence the verdict.
CREATIVE_TEMPERATURE = float(os.environ.get("VENTUREBOT_CREATIVE_TEMPERATURE", "1.0"))

# ── Models: Phase 2 (OpenRouter) ───────────────────────────────────────
MODEL_PO = os.environ.get("VENTUREBOT_MODEL_PO", "deepseek/deepseek-v4-pro")
MODEL_TESTWRITER = os.environ.get(
    "VENTUREBOT_MODEL_TESTWRITER", "deepseek/deepseek-chat-v3-0324"
)
MODEL_CODER = os.environ.get("VENTUREBOT_MODEL_CODER", "deepseek/deepseek-chat-v3-0324")
MODEL_QA = os.environ.get("VENTUREBOT_MODEL_QA", "deepseek/deepseek-v4-pro")

OPENROUTER_BASE = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# ── Auth ───────────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
# Server-side OAuth secret (A6/G6): NEVER shipped to the browser. Required
# for the authorization-code flow exchange; without it login returns 503.
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
ALLOWED_EMAILS = [
    e.strip().lower()
    for e in os.environ.get("VENTUREBOT_ALLOWED_EMAILS", "").split(",")
    if e.strip()
]
# Operator kill-switch: freeze NEW registrations while existing users keep
# logging in (replaces the hard email allowlist as the primary gate).
SIGNUP_CLOSED = os.environ.get("VENTUREBOT_SIGNUP_CLOSED", "false").lower() in ("1", "true", "yes")
COOKIE_SECURE = os.environ.get("VENTUREBOT_COOKIE_SECURE", "false").lower() in ("1", "true", "yes")
# Public base URL (behind nginx). Used to build the OAuth redirect URI.
# Falls back to deriving from X-Forwarded-* / Host headers when unset.
PUBLIC_BASE_URL = os.environ.get("VENTUREBOT_PUBLIC_BASE_URL", "").strip().rstrip("/")

# Prototype phase: auth disabled (single-user, no login). Set to 0 to re-enable
# Google SSO when the multi-user feature lands. Default ON for the prototype.
NO_AUTH = os.environ.get("VENTUREBOT_NO_AUTH", "1").strip().lower() in ("1", "true", "yes", "")

# ── Credential resolution ──────────────────────────────────────────────
def google_api_key() -> str:
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "No GOOGLE_API_KEY found. Set it in .env (ADK Gemini models read "
            "GOOGLE_API_KEY, not GEMINI_API_KEY)."
        )
    return key


def openrouter_api_key() -> str:
    env_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if env_key:
        return env_key
    auth_path = Path.home() / ".pi" / "agent" / "auth.json"
    if auth_path.exists():
        try:
            data = json.loads(auth_path.read_text())
            key = data.get("openrouter", {}).get("key", "").strip()
            if key:
                return key
        except (json.JSONDecodeError, OSError):
            pass
    raise RuntimeError(
        "No OpenRouter API key found. Set OPENROUTER_API_KEY or ensure "
        "~/.pi/agent/auth.json contains an 'openrouter' entry with a 'key'."
    )
