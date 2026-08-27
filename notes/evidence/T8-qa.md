# T8 QA — Adversarial Verification (BYOK Key UX)

QA agent: adversarial verifier, unattended.
Date: 2026-08-27
Task: T8 — BYOK key UX (key entry, verify-before-run, localStorage-only, never rendered back)

## Worker's claim (treated as UNTRUSTED)

Worker claims (notes/evidence/T8-worker.md):
- Added `frontend/src/byok.ts` — key entry + verify + localStorage module
- Modified `frontend/src/api.ts` — added `verifyByokKey(path)`
- Modified `frontend/src/app.ts` — composition root wires key UX, gates Run button, removed `sk-or-v1-demo-placeholder` fallback
- Modified `templates/index.html` — added `#key-card` key-entry section
- Added `tests/e2e/byok.spec.md` — E2E spec
- Rebuilt `static/app.js` (esbuild bundle)
- Full suite: 200 passed
- grep `api_key` in bundle: exactly 2 call sites
- TypeScript strict compile: clean

## Independent verification (QA ran everything fresh)

### 1. Full test suite
```
$ timeout 300 venv/bin/python -m pytest tests/ -q
200 passed in 3.20s
```
✅ PASS

### 2. grep frontend bundle: api_key count
```
$ grep -o "api_key" static/app.js | wc -l
2
```
Exactly 2 occurrences — the verify endpoint and the create-debate endpoint. No extras.
✅ PASS

### 3. sk-or-v1-demo-placeholder removal (D1 compliance)
```
$ grep -r "sk-or-v1-demo-placeholder" static/ frontend/ templates/
(no matches, exit 1)
```
Completely removed. The diff of `app.ts` confirms the old `currentKey()` function
that returned `'sk-or-v1-demo-placeholder'` as fallback was replaced by the BYOK
module's `storedKey()` which returns `null` when no key is stored.
✅ PASS

### 4. Server-key fallback check (D1)
```
$ grep -n "fallback\|server.key\|SERVER_KEY\|GOOGLE_API_KEY" frontend/src/byok.ts frontend/src/app.ts frontend/src/api.ts
(no matches, exit 1)
```
No server-key fallback exists in any frontend file. The only mentions of
"no server-key fallback" are in `src/dashboard.py` (backend documentation
comments correctly stating D1 compliance).
✅ PASS

### 5. CDN/external script references (S9)
```
$ grep -rn "cdn\.\|unpkg\|jsdelivr" templates/ static/
(no matches in T8-changed files)
```
The only match is in `static/vendor/tailwind-3.4.16.min.js` — the vendored
library's own internal `console.warn("cdn.tailwindcss.com...")` warning.
This is NOT a CDN reference from our code; it's the vendored library's
internal production warning. Already accepted by T4 QA.
✅ PASS

### 6. Console.log in BYOK files
```
$ grep -n "console\.log" frontend/src/byok.ts frontend/src/app.ts frontend/src/api.ts
(no matches, exit 1)
```
No console.log statements. Key material is never logged.
✅ PASS

### 7. Key input security
- `templates/index.html`: `<input id="key-input" type="password">` — confirmed.
- `autocomplete="off"` and `spellcheck="false"` — confirmed.
- After verify: `input.value = ''` — confirmed in `app.ts` line 71.
- Key never rendered to DOM: `setKeyStatus()` only sets generic status text
  ("API key saved in this browser only."), never the key literal.
✅ PASS

### 8. TypeScript compilation
```
$ cd frontend && npx tsc --noEmit
EXIT: 0 (clean)
```
✅ PASS

### 9. Bundle rebuild
```
$ cd frontend && npm run build
  ../static/app.js  12.6kb
  ⚡ Done in 13ms
EXIT: 0
```
Bundle matches worker's claim (12.6kb).
✅ PASS

### 10. Scope check (git status / git diff)
```
Modified:
  frontend/src/api.ts      — added verifyByokKey function (+6 lines)
  frontend/src/app.ts      — BYOK module integration, removed placeholder (+106 -19 lines)
  notes/TASKBOARD.md       — status update
  static/app.js            — rebuilt bundle
  templates/index.html     — added #key-card section (+20 lines)
New:
  frontend/src/byok.ts     — BYOK module (new)
  notes/evidence/T8-worker.md — worker evidence
  tests/e2e/byok.spec.md   — E2E spec
```
All changes are within T8 scope. No unrelated modifications.
✅ PASS

### 11. Dead code / silent error check
- `byok.ts` `readStored()` catch → returns `undefined` (graceful degradation for
  storage-unavailable environments; `keyState()` re-reads from storage each time
  so it correctly reports `saved: false`).
- `byok.ts` `setStored()` catch → empty body with comment "storage blocked
  (private mode)". This is acceptable: `keyState()` re-reads from storage,
  so `saved` will correctly be `false` if storage is blocked.
- No `except: pass` patterns in any frontend file.
- No dead code detected.
✅ PASS

### 12. E2E spec review (tests/e2e/byok.spec.md)
- Scenario A: wrong/missing key → explicit blocking message, run cannot start,
  invalid key un-stored. 8 steps, all assertions meaningful.
- Scenario B: valid key → saved in localStorage, never rendered back, run starts
  only after verify. 6 steps, all assertions meaningful.
- No stub assertions, no always-pass checks.
✅ PASS

### 13. Design observation (not a failure)
The `validated` flag is intentionally NOT persisted across page reloads. This
means users must re-verify their key each session. The E2E spec acknowledges
this: "verified keys require re-verification after page reload." This is a
security-conscious trade-off (prevents stale validation if the key is revoked
server-side) at the cost of one extra API call per session. Documented in
`byok.ts` comments. Acceptable for v1.

## Summary

| Check | Result |
|-------|--------|
| Full test suite (200 tests) | ✅ PASS |
| api_key grep count = 2 | ✅ PASS |
| sk-or-v1-demo-placeholder removed | ✅ PASS |
| No server-key fallback (D1) | ✅ PASS |
| No CDN references (S9) | ✅ PASS |
| No console.log of key material | ✅ PASS |
| Key input type=password + autocomplete=off | ✅ PASS |
| Key never rendered back to DOM | ✅ PASS |
| TypeScript strict compile clean | ✅ PASS |
| Bundle rebuild matches (12.6kb) | ✅ PASS |
| Scope: only T8 changes | ✅ PASS |
| No dead code / silent failures | ✅ PASS |
| E2E spec meaningful | ✅ PASS |

VERDICT: PASS
