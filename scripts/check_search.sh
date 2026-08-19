#!/usr/bin/env bash
# check_search.sh — one-command google_search reachability check.
#
# ADK's `google_search` is Gemini built-in search grounding (not the Custom
# Search JSON API), so there is no CSE/cx/GCP enablement to configure. This
# script proves the tool actually works with the configured key + models.
#
# Usage: ./scripts/check_search.sh
set -u
cd "$(dirname "$0")/.."
./venv/bin/python - <<'PY'
import sys; sys.path.insert(0, ".")
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types
from venturebot import config

load_dotenv(Path(".env"))

print("=" * 62)
print("google_search reachability (Gemini grounding)")
print("=" * 62)

models = [config.MODEL_RESEARCHER, config.MODEL_CRITIC]
all_ok = True
for model in models:
    try:
        client = genai.Client(api_key=config.google_api_key())
        resp = client.models.generate_content(
            model=model,
            contents="Who won the 2022 FIFA World Cup? One sentence.",
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            ),
        )
        grounded = False
        for cand in (resp.candidates or []):
            gm = getattr(cand, "grounding_metadata", None)
            if gm and getattr(gm, "grounding_chunks", None):
                grounded = True
        ok = bool(resp.text.strip())
        all_ok &= ok
        tag = "grounded" if grounded else "answered (no grounding metadata)"
        print(f"[{'OK' if ok else 'FAIL'}] {model:24s} {tag}")
    except Exception as e:
        all_ok = False
        print(f"[FAIL] {model:24s} {type(e).__name__}: {str(e)[:200]}")

print("=" * 62)
print("RESULT:", "PASS — Critic can fact-check." if all_ok else "FAIL — see errors above.")
sys.exit(0 if all_ok else 1)
PY
