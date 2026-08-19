#!/usr/bin/env bash
# VentureBot secret scanner — the automated version of the manual grep gate.
# Scans staged diff (pre-commit), HEAD (pre-push), or arbitrary files/paths.
# Exit 0 = clean, exit 1 = secret found.
set -u

# Patterns for common secrets. Keep this list aggressive-but-precise:
#  - sk-or-... (OpenRouter), sk-... (generic)
#  - AIza... (Google API keys)
#  - AQ... (Google AI Studio keys)
#  - ghp_... / github_pat_... (GitHub)
#  - AWS AKIA... , private key blocks
#  - Supabase service_role / anon JWT-ish keys
PATTERNS=(
  'sk-[A-Za-z0-9]{16,}'
  'sk-or-v1-[A-Za-z0-9]{16,}'
  'AIza[0-9A-Za-z_-]{20,}'
  'AQ[0-9A-Za-z_-]{20,}'
  'ghp_[A-Za-z0-9]{20,}'
  'github_pat_[A-Za-z0-9_]{20,}'
  'AKIA[0-9A-Z]{16}'
  'BEGIN [A-Z ]*PRIVATE KEY'
  'service_role[":= ]+[A-Za-z0-9._-]{20,}'
)

scan_text() {
  local text="$1"
  for p in "${PATTERNS[@]}"; do
    if echo "$text" | grep -nE "$p" >/dev/null 2>&1; then
      echo "$text" | grep -nE "$p" | sed 's/\(.\{0,40\}\).*/\1…[REDACTED]/'
      return 1
    fi
  done
  return 0
}

mode="${1:-}"
shift 2>/dev/null || true
targets=("$@")

case "$mode" in
  --staged)
    # Pre-commit: scan the staged diff (both added content and file names)
    text=$(git diff --cached --unified=0 2>/dev/null)
    names=$(git diff --cached --name-only 2>/dev/null)
    ;;
  --head)
    # Pre-push: scan everything tracked in HEAD
    text=$(git grep -nE '' HEAD 2>/dev/null || git grep -nE '.' 2>/dev/null)
    names=$(git ls-files 2>/dev/null)
    ;;
  --files)
    text=""
    for t in "${targets[@]}"; do
      [ -f "$t" ] && text+="$(cat "$t" 2>/dev/null)"$'\n'
    done
    names="${targets[*]}"
    ;;
  *)
    echo "Usage: $0 --staged | --head | --files <path...>" >&2
    exit 2
    ;;
esac

# Scan file NAMES too (a file named `sk-...` is itself a leak)
if echo "$names" | grep -nE 'sk-[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_-]{20,}|ghp_|github_pat_' >/dev/null 2>&1; then
  echo "SECRET LEAK in filename:" >&2
  echo "$names" | grep -nE 'sk-[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_-]{20,}|ghp_|github_pat_'
  exit 1
fi

# Scan content
if scan_text "$text"; then
  echo "secret-scan: CLEAN"
  exit 0
else
  echo "" >&2
  echo "⚠ SECRET LEAK DETECTED — aborting. Remove the secret and re-run." >&2
  exit 1
fi
