/**
 * app-shell.ts — VentureBot app shell (T6).
 *
 * Scope of T6:
 *   - IndexedDB CRUD for ideas (client-side store, decision D2).
 *   - JSON export/import (the user's backup mechanism).
 *   - Client-side duplicate check (idk locally, no LLM).
 *
 * The live debate tab, BYOK key flow and disconnect recovery are separate
 * tasks (T7/T8/T9). This file is intentionally a thin shell that owns the
 * DOM for the idea list + the add / export / import / delete controls.
 */
import * as store from './store';
import type { Idea } from './idb';
import { byId, dom } from './dom';

const IDEA_INPUT = 'idea-input';
const IDEA_LIST = 'ideas-list';
const IDEA_COUNT = 'idea-count';
const EXPORT_BTN = 'btn-export';
const IMPORT_FILE = 'import-file';
const IMPORT_BTN = 'btn-import';
const STATUS = 'status-line';
const CLEAR_BTN = 'btn-clear';
const DUPLICATE_HINT = 'duplicate-hint';
const SUBMIT_BTN = 'btn-add';

let ideas: Idea[] = [];

function renderList(): void {
  const list = byId<HTMLDivElement>(IDEA_LIST);
  list.innerHTML = '';
  for (const idea of ideas) {
    const row = dom('div', 'idea-row flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-900 border border-slate-700/60');
    const text = dom('span', 'flex-1 text-sm text-slate-100', idea.title);
    const del = dom('button', 'px-2 py-1 text-xs rounded bg-slate-700 hover:bg-slate-600 text-white', '✕');
    del.setAttribute('data-id', idea.id);
    del.addEventListener('click', () => void removeIdea(idea.id));
    row.appendChild(text);
    row.appendChild(del);
    list.appendChild(row);
  }
  byId(IDEA_COUNT).textContent = `${ideas.length} idea${ideas.length === 1 ? '' : 's'}`;
  const empty = byId('ideas-empty');
  empty.classList.toggle('hidden', ideas.length > 0);
}

async function addIdea(): Promise<void> {
  const input = byId<HTMLInputElement>(IDEA_INPUT);
  const text = input.value.trim();
  const status = byId(STATUS);
  const hint = byId(DUPLICATE_HINT);
  hint.classList.add('hidden');
  if (!text) return;

  const outcome = await store.addIdea(text);
  if (outcome.reason === 'duplicate') {
    hint.textContent =
      `Duplicate: an idea with this exact title ("${outcome.existingTitle ?? ''}") already exists.`;
    hint.classList.remove('hidden');
    return;
  }
  if (outcome.reason === 'empty') return;
  input.value = '';
  status.textContent = `📝 Saved "${text}"`;
  await refresh();
}

async function refresh(): Promise<void> {
  ideas = await store.listIdeas();
  renderList();
}

async function removeIdea(id: string): Promise<void> {
  await store.removeIdea(id);
  await refresh();
}

function exportJson(): void {
  store.exportJson().then((blob) => {
    const a = document.createElement('a');
    const url = URL.createObjectURL(new Blob([blob], { type: 'application/json' }));
    a.href = url;
    a.download = `venturebot-ideas-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }).catch((err) => {
    byId(STATUS).textContent = `Export failed: ${String(err)}`;
  });
}

async function onImportFile(file: File): Promise<void> {
  try {
    const text = await file.text();
    const summary = await store.importJson(text);
    byId(STATUS).textContent =
      `Import: ${summary.added} added, ${summary.existing} already present (skipped).`;
    await refresh();
  } catch (err) {
    byId(STATUS).textContent = `Import failed: ${String(err)}`;
  }
}

function wire(): void {
  byId(SUBMIT_BTN).addEventListener('click', () => void addIdea());
  byId(IDEA_INPUT).addEventListener('keydown', (e) => {
    if (e.key === 'Enter') void addIdea();
  });
  byId(EXPORT_BTN).addEventListener('click', exportJson);
  byId(IMPORT_BTN).addEventListener('click', () => byId<HTMLInputElement>(IMPORT_FILE).click());
  byId<HTMLInputElement>(IMPORT_FILE).addEventListener('change', async (e) => {
    const f = (e.target as HTMLInputElement).files?.[0];
    if (f) await onImportFile(f);
    (e.target as HTMLInputElement).value = '';
  });
  byId(CLEAR_BTN).addEventListener('click', async () => {
    if (window.confirm('Delete ALL ideas in this browser? This cannot be undone (no server copy exists).')) {
      await store.wipe();
      await refresh();
    }
  });
  byId(IDEA_INPUT).addEventListener('input', () => void maybeDuplicateHint());
}

async function maybeDuplicateHint(): Promise<void> {
  const input = byId<HTMLInputElement>(IDEA_INPUT);
  const hint = byId(DUPLICATE_HINT);
  const t = input.value.trim();
  if (!t) {
    hint.classList.add('hidden');
    return;
  }
  const existing = await store.findDuplicate(t);
  if (existing) {
    hint.textContent = `Tip: "${existing.title}" is already in your list.`;
    hint.classList.remove('hidden');
  } else {
    hint.classList.add('hidden');
  }
}

export function init(): void {
  wire();
  void refresh();
}