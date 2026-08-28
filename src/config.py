"""VentureBot configuration.

Loads non-secret defaults from config.json (committed to git), then overrides
with environment variables. Secrets (GOOGLE_API_KEY, GOOGLE_CLIENT_SECRET,
OPENROUTER_API_KEY) come ONLY from environment  -- they are never in config.json.

Usage:
  Every setting is available as config.SETTING_NAME.
  All env overrides use VENTUREBOT_<UPPER_CASE> naming (e.g. VENTUREBOT_MAX_ITERATIONS=10).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env into os.environ for local dev (Cloud Run sets real env vars)
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass  # dotenv not installed in production  -- env vars come from Cloud Run

# -- Load non-secret defaults from committed config.json -----------------

def _load_config_json() -> dict:
    path = BASE_DIR / "config.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}

_cfg = _load_config_json()

# Strip comments (keys starting with //)
_cfg = {k: v for k, v in _cfg.items() if not k.startswith("//")}

def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()

def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))

def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, str(default)))

def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name, str(default)).strip().lower()
    return val in ("1", "true", "yes")

def _env_list(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [e.strip().lower() for e in raw.split(",") if e.strip()]

# -- Paths --------------------------------------------------------------

WORKSPACE_DIR = Path(_env("VENTUREBOT_WORKSPACE", _cfg.get("workspace_dir", "workspace")))
DATA_DIR      = Path(_env("VENTUREBOT_DATA", _cfg.get("data_dir", "data")))

# -- Loop & Timeout budget ----------------------------------------------

RUN_DEADLINE_SECONDS           = _env_int("VENTUREBOT_RUN_DEADLINE", _cfg.get("run_deadline_seconds", 900))
LLM_TIMEOUT                    = _env_int("VENTUREBOT_LLM_TIMEOUT", _cfg.get("llm_timeout", 120))
RUN_RETRY_ATTEMPTS             = _env_int("VENTUREBOT_RUN_RETRY_ATTEMPTS", _cfg.get("run_retry_attempts", 3))
RUN_TTL_SECONDS                = _env_int("VENTUREBOT_RUN_TTL_SECONDS", _cfg.get("run_ttl_seconds", 24 * 3600))
PAUSE_MAX_AGE_SECONDS          = _env_int("VENTUREBOT_PAUSE_MAX_AGE_SECONDS", _cfg.get("pause_max_age_seconds", 7 * 86400))
GOOGLE_VERIFY_TIMEOUT_SECONDS  = _env_float("VENTUREBOT_GOOGLE_VERIFY_TIMEOUT", _cfg.get("google_verify_timeout", 8.0))

# -- Orchestrator loop budget --------------------------------------------

ORCHESTRATOR_MAX_TURNS      = _env_int("VENTUREBOT_ORCHESTRATOR_MAX_TURNS", _cfg.get("orchestrator_max_turns", 10))
ORCHESTRATOR_MAX_TOOL_CALLS = _env_int("VENTUREBOT_ORCHESTRATOR_MAX_TOOL_CALLS", _cfg.get("orchestrator_max_tool_calls", 50))
ORCHESTRATOR_STALL_TURNS    = _env_int("VENTUREBOT_ORCHESTRATOR_STALL_TURNS", _cfg.get("orchestrator_stall_turns", 3))

# -- Models: Phase 1 (Gemini / ADK) -------------------------------------

MODEL_RESEARCHER  = _env("VENTUREBOT_MODEL_RESEARCHER", _cfg.get("model_researcher", "gemini-3.7-flash"))
MODEL_ADVOCATE    = _env("VENTUREBOT_MODEL_ADVOCATE", _cfg.get("model_advocate", "gemini-3.7-flash"))
MODEL_CRITIC      = _env("VENTUREBOT_MODEL_CRITIC", _cfg.get("model_critic", "gemini-3.1-pro-preview"))
MODEL_JUDGE       = _env("VENTUREBOT_MODEL_JUDGE", _cfg.get("model_judge", "gemini-3.1-pro-preview"))
MODEL_PRD_WRITER  = _env("VENTUREBOT_MODEL_PRD_WRITER", _cfg.get("model_prd_writer", "gemini-3.1-pro-preview"))
MODEL_AUDITOR     = _env("VENTUREBOT_MODEL_AUDITOR", _cfg.get("model_auditor", "gemini-3.1-pro-preview"))
MODEL_CREATIVE    = _env("VENTUREBOT_MODEL_CREATIVE", _cfg.get("model_creative", "gemini-3.7-flash"))

# -- Model: Orchestrator -------------------------------------------------

MODEL_ORCHESTRATOR = _env("VENTUREBOT_MODEL_ORCHESTRATOR", _cfg.get("model_orchestrator", "gemini-3.1-pro-preview"))

# -- Temperatures -------------------------------------------------------

CREATIVE_TEMPERATURE = _env_float("VENTUREBOT_CREATIVE_TEMPERATURE", _cfg.get("creative_temperature", 1.0))

# -- Deployment & Security ----------------------------------------------

COOKIE_SECURE        = _env_bool("VENTUREBOT_COOKIE_SECURE", _cfg.get("cookie_secure", False))
PUBLIC_BASE_URL      = _env("VENTUREBOT_PUBLIC_BASE_URL", _cfg.get("public_base_url", ""))
NO_AUTH              = True

# -- Credential resolution (secrets  -- environment ONLY) -----------------

def google_api_key() -> str:
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "No GOOGLE_API_KEY found. Set it in .env (ADK Gemini models read "
            "GOOGLE_API_KEY, not GEMINI_API_KEY)."
        )
    return key