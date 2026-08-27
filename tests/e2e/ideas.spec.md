# T6 E2E — Idea store (app shell + IndexedDB ideas + export/import + duplicate check)

Checklist run via agent-browser against the local server (`src/dashboard:app`).

## Setup
- The app page is served at `/app` (dashboard route that reads `templates/index.html`
  and the esbuild bundle at `/static/app.js`).
- The idea store lives in browser IndexedDB `venturebot`, object store `ideas`.
- Start the server first:
  ```
  VENTUREBOT_NO_AUTH=1 ./venv/bin/uvicorn src.dashboard:app --host 127.0.0.1 --port 8399
  ```

## Steps

### 1. Create two ideas via the UI
1. Open http://127.0.0.1:8399/app
2. In `#idea-input` type `A CLI tool that summarizes git diffs into plain English`
   and click `#btn-add`.
3. In `#idea-input` type `An app that helps gardeners track watering schedules`
   and click `#btn-add`.
4. Assert `#ideas-list` shows *both* entries with their texts.
5. Assert `#idea-count` reads `2 ideas`.

### 2. Reload persists both (IndexedDB durability)
6. Reload the page (browser refresh).
7. Assert `#ideas-list` still shows both ideas (`#idea-count` = `2 ideas`).

### 3. Duplicate submit warns
8. Enter the exact same text as idea #1 again and submit.
9. Assert a duplicate warning appears (`#duplicate-hint` becomes visible and
   mentions "already").

### 4. Export JSON
10. Click `#btn-export`. A JSON file download is produced.
11. Inspect the downloaded JSON: it is an object with `format:
    'venturebot-ideas'`, `count` = 2 and `ideas` an array of 2 idea records each
    with `id`, `title`, `createdAt`, `updatedAt`.

### 5. Import restores ideas in a clean profile
1. Export the ideas (step 4) to a known file path.
2. On a *clean* profile (fresh browser context / cleared IndexedDB), open the app.
3. Assert `#ideas-list` is empty (`#idea-count` = `0 ideas`).
4. Click `#btn-import` and select the exported JSON file.
5. Assert `#ideas-list` now shows the two ideas again and `#idea-count` = `2`
   ideas — i.e. the JSON backup restored them in a different profile.

## Pass criteria
- A: both ideas survive a reload
- B: duplicate submit warns (client-side; no network call needed)
- C: exported JSON re-imported in a clean profile restores both ideas.