/**
 * IdeaStore — the client-side view of truth for ideas (D2).
 *
 * Ideas live ONLY in the browser's IndexedDB; there is no server idea table.
 * This module is the frontend contract for T6: CRUD, JSON export/import and
 * the client-side duplicate check. All functions are small on purpose and
 * each one maps to a named verification step in tests/e2e/ideas.spec.md.
 */
import * as idb from './idb';
import type { Idea } from './idb';

export interface ExportFile {
  format: 'venturebot-ideas';
  version: number;
  exportedAt: string;
  count: number;
  ideas: Idea[];
}

export interface AddOutcome {
  ok: boolean;
  reason?: 'duplicate' | 'empty';
  idea?: Idea;
  /** Title of the existing idea that blocked the add (when reason='duplicate'). */
  existingTitle?: string;
}

export interface ImportSummary {
  added: number;
  existing: number;
}

/** All ideas, newest first. */
export function listIdeas(): Promise<Idea[]> {
  return idb.getAllIdeas();
}

/**
 * Add a brand new idea. Returns {ok:false, reason:'duplicate'} when an idea
 * with the same normalized title already exists; returns {ok:false,
 * reason:'empty'} for blank input. The UI uses this to warn the duplicate.
 */
export async function addIdea(pitch: string): Promise<AddOutcome> {
  const title = pitch.trim();
  if (!title) return { ok: false, reason: 'empty' };

  const dup = await findDuplicate(title);
  if (dup) return { ok: false, reason: 'duplicate', existingTitle: dup.title };

  const idea = idb.newIdea(title);
  await idb.putIdea(idea);
  return { ok: true, idea };
}

export function removeIdea(id: string): Promise<void> {
  return idb.deleteIdea(id);
}

/**
 * Client-side duplicate pre-check used at submit time: returns the existing
 * idea's title when the input matches, else null. Purely local — no LLM.
 */
export async function findDuplicate(title: string): Promise<Idea | null> {
  const t = title.trim().toLowerCase();
  if (!t) return null;
  const ideas = await idb.getAllIdeas();
  return ideas.find((i) => i.title.trim().toLowerCase() === t) ?? null;
}

/** Build the JSON backup blob. */
export async function exportJson(): Promise<string> {
  const ideas = await idb.getAllIdeas();
  const payload: ExportFile = {
    format: 'venturebot-ideas',
    version: 1,
    exportedAt: new Date().toISOString(),
    count: ideas.length,
    ideas,
  };
  return JSON.stringify(payload, null, 2);
}

/** Merge a validated export back into the store. */
export async function importJson(text: string): Promise<ImportSummary> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error('Import file is not valid JSON');
  }
  const doc = parsed as Partial<ExportFile>;
  const ideas = Array.isArray(doc.ideas) ? doc.ideas.filter(isIdea) : [];
  let added = 0;
  let existing = 0;
  for (const idea of ideas) {
    // Idempotent import: skip anything already present (by id or title).
    const present = (await idb.getIdea(idea.id)) ?? (await findDuplicate(idea.title));
    if (present) {
      existing++;
      continue;
    }
    await idb.putIdea(idea);
    added++;
  }
  return { added, existing };
}

function isIdea(x: unknown): x is Idea {
  if (!x || typeof x !== 'object') return false;
  const o = x as Record<string, unknown>;
  return typeof o.title === 'string' && o.title.trim() !== '' && typeof o.id === 'string';
}

/** Wipe the whole store (clear action / tests). */
export function wipe(): Promise<void> {
  return idb.deleteDatabase();
}