# Coding Rule: ASCII-Only Source Code

## Rule

All source code (`.py`, `.js`, `.ts`, `.html`, `.css`, `.json`, `.yaml`, `.sh`, `.tf`, `Dockerfile`, `Makefile`, etc.) must use **only ASCII characters** (U+0000–U+007F).

## Exceptions

These non-ASCII characters are allowed because they serve a specific purpose visible to the user:

| Character | Use | Example |
|-----------|-----|---------|
| Emojis | UI labels, log messages, error output intended for humans | `✅ "PRD complete"` |
| `…` (ellipsis, U+2026) | Placeholder text in HTML `placeholder="Search…"` | Input field hints |
| `•` (bullet, U+2022) | Simple inline list markers in rendered UI text | `• First item` |
| `×` (multiply, U+00D7) | Close/cancel buttons | `×` button |
| `←` `→` `↑` `↓` (arrows) | Navigation buttons | `← Back` |
| `‹` `›` `▸` `◂` | Pagination, disclosure triangles | `Next ›` |
| `✓` `✗` `✏️` `⬇` `⬆` | Action buttons | `⬇ Download` |

## Replacements

For everything else, use ASCII equivalents:

| Instead of | Use | Note |
|-----------|-----|------|
| `—` em dash | `---` or ` -- ` | In prose-like comments |
| `–` en dash | `-` | In ranges like `G5–G7` → `G5-G7` |
| `→` right arrow | `->` | In comments/docstrings |
| `§` section sign | `Sec.` or just omit | `PRD §5.4` → `PRD Sec. 5.4` |
| `─` `━` `┄` `┅` box chars | `-` | In ASCII-art separators |
| `≥` `≤` `≠` math symbols | `>=` `<=` `!=` | In all contexts |
| `…` ellipsis (in code) | `...` | In Python/JS strings, not HTML placeholders |
| `é` `ü` `ñ` accented chars | `e` `u` `n` | In comments/docstrings |
| `"` `"` curly quotes | `"` straight quotes | Everywhere |
| `'` `'` curly apostrophe | `'` straight apostrophe | Everywhere |
| `•` bullet (in code) | `*` or `-` | In comments, not UI |

## Rationale

1. **Portability** — ASCII works on every system, every editor, every terminal, every font.
2. **Git diff clarity** — Non-ASCII changes are invisible or misleading in diffs.
3. **No encoding surprises** — Avoids `UnicodeDecodeError`, BOM issues, locale problems.
4. **Search/grep** — ASCII characters are unambiguous to search with regex.
5. **Terminal output** — Cloud Run logs, journald, and terminal emulators handle ASCII perfectly; some strip or mangle non-ASCII.

## Enforcement

- Before committing, run: `grep -rPn '[^\x00-\x7F]' src/ tests/ --include='*.py' --include='*.js' --include='*.html'`
- If the output contains anything other than allowed emojis/symbols, replace it.