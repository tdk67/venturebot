/**
 * IdeaStore — Client-side idea storage & export/import for Idea Lint (D2).
 */
import * as idb from './idb';
import type { Idea, IdeaRun } from './idb';
import {
  CURRENT_EXPORT_FORMAT,
  CURRENT_EXPORT_VERSION,
  migrateToLatest,
  type ExportEnvelope,
} from './migrations';
import { formatDocText } from './debate';

export type ExportFile = ExportEnvelope;

export interface AddOutcome {
  ok: boolean;
  reason?: 'duplicate' | 'empty';
  idea?: Idea;
  existingTitle?: string;
}

export interface ImportSummary {
  added: number;
  existing: number;
  migratedCount: number;
  schemaVersion: number;
}

/** All ideas, newest first. */
export function listIdeas(): Promise<Idea[]> {
  return idb.getAllIdeas();
}

/** Get a single idea by ID. */
export function getIdea(id: string): Promise<Idea | undefined> {
  return idb.getIdea(id);
}

/** Add a brand new idea. */
export async function addIdea(pitch: string, urls?: string[]): Promise<AddOutcome> {
  const title = pitch.trim();
  if (!title) return { ok: false, reason: 'empty' };

  const dup = await findDuplicate(title);
  if (dup) return { ok: false, reason: 'duplicate', existingTitle: dup.title };

  const idea = idb.newIdea(title, urls);
  await idb.putIdea(idea);
  return { ok: true, idea };
}

/** Update an existing idea. */
export async function updateIdea(idea: Idea): Promise<void> {
  idea.updatedAt = Date.now();
  await idb.putIdea(idea);
}

/** Delete an idea. */
export function removeIdea(id: string): Promise<void> {
  return idb.deleteIdea(id);
}

/** Record a completed or in-progress debate run onto the idea. */
export async function saveRunResult(ideaId: string, run: IdeaRun): Promise<Idea | null> {
  const idea = await idb.getIdea(ideaId);
  if (!idea) return null;

  if (!idea.runs) idea.runs = [];
  const existingIdx = idea.runs.findIndex((r) => r.run_id === run.run_id);
  if (existingIdx >= 0) {
    idea.runs[existingIdx] = { ...idea.runs[existingIdx], ...run };
  } else {
    run.run_number = idea.runs.length + 1;
    idea.runs.push(run);
  }

  idea.latestRunId = run.run_id;
  if (run.verdict) {
    idea.verdict = run.verdict;
    const vUpper = run.verdict.toUpperCase();
    if (vUpper.includes('PROCEED')) idea.status = 'PROCEED';
    else if (vUpper.includes('PARK')) idea.status = 'PARK';
    else if (vUpper.includes('PRUNE')) idea.status = 'PRUNE';
  }
  if (run.scores) idea.scores = run.scores;
  if (run.prd_text) idea.prd_text = run.prd_text;

  idea.updatedAt = Date.now();
  await idb.putIdea(idea);
  return idea;
}

/** Find duplicate title. */
export async function findDuplicate(title: string): Promise<Idea | null> {
  const t = title.trim().toLowerCase();
  if (!t) return null;
  const ideas = await idb.getAllIdeas();
  return ideas.find((i) => i.title.trim().toLowerCase() === t) ?? null;
}

/**
 * Build JSON backup blob.
 * ALWAYS saves adhering to the latest schema version (CURRENT_EXPORT_VERSION).
 */
export async function exportJson(): Promise<string> {
  const ideas = await idb.getAllIdeas();
  const payload: ExportEnvelope = {
    format: CURRENT_EXPORT_FORMAT,
    version: CURRENT_EXPORT_VERSION,
    exportedAt: new Date().toISOString(),
    count: ideas.length,
    ideas,
  };
  return JSON.stringify(payload, null, 2);
}

/**
 * Merge JSON export into store with backward-compatible migration.
 * Converts any older version (V1, V2...) to the latest schema before writing to IndexedDB.
 */
export async function importJson(text: string): Promise<ImportSummary> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error('Import file is not valid JSON');
  }

  const migratedEnvelope = migrateToLatest(parsed);
  const ideas = migratedEnvelope.ideas;

  let added = 0;
  let existing = 0;

  for (const idea of ideas) {
    const present = (await idb.getIdea(idea.id)) ?? (await findDuplicate(idea.title));
    if (present) {
      existing++;
      continue;
    }
    await idb.putIdea(idea);
    added++;
  }

  return {
    added,
    existing,
    migratedCount: ideas.length,
    schemaVersion: migratedEnvelope.version,
  };
}

/** Export ideas as CSV. */
export async function exportCsv(): Promise<string> {
  const ideas = await idb.getAllIdeas();
  const rows = [
    ['ID', 'Title', 'Status', 'Verdict', 'Novelty', 'Feasibility', 'Market Fit', 'Created At', 'Runs Count'].join(','),
  ];
  for (const i of ideas) {
    const sc = i.scores || {};
    const dateStr = new Date(i.createdAt).toISOString().slice(0, 10);
    const row = [
      `"${i.id}"`,
      `"${i.title.replace(/"/g, '""')}"`,
      `"${i.status || 'ACTIVE'}"`,
      `"${i.verdict || ''}"`,
      sc.novelty ?? '',
      sc.feasibility ?? '',
      sc.market_fit ?? '',
      `"${dateStr}"`,
      (i.runs || []).length,
    ];
    rows.push(row.join(','));
  }
  return rows.join('\n');
}

/** Build comprehensive Markdown debate report. */
export function buildRunMarkdown(idea: Idea, run: IdeaRun): string {
  const lines: string[] = [];
  lines.push(`# Idea Lint Debate Report: ${idea.title}`);
  lines.push('');
  lines.push(`- **Status:** ${run.status || 'DONE'}`);
  if (run.verdict) lines.push(`- **Verdict:** ${run.verdict}`);
  if (run.scores) {
    lines.push(
      `- **Scores (out of 10):** Novelty: ${run.scores.novelty ?? '—'} · Feasibility: ${
        run.scores.feasibility ?? '—'
      } · Market Fit: ${run.scores.market_fit ?? '—'}`,
    );
  }
  if (run.comment) lines.push(`- **Human steering comment:** ${run.comment}`);
  const date = run.finished_at ? new Date(run.finished_at) : new Date(run.started_at);
  lines.push(`- **Date:** ${date.toLocaleString()}`);
  if (run.turns_used) lines.push(`- **Orchestrator turns:** ${run.turns_used}`);
  lines.push('');

  if (run.prd_text) {
    lines.push('## Product Requirements Document (PRD)', '', formatDocText(run.prd_text), '');
  }
  if (run.research_brief) {
    lines.push('## Research Brief', '', formatDocText(run.research_brief), '');
  }
  if (run.advocate_argument) {
    lines.push('## Advocate Argument', '', formatDocText(run.advocate_argument), '');
  }
  if (run.critic_rebuttal) {
    lines.push('## Critic Rebuttal', '', formatDocText(run.critic_rebuttal), '');
  }
  if (run.creative_angles) {
    lines.push('## Creative Angles & Pivots', '', formatDocText(run.creative_angles), '');
  }

  if (run.events && run.events.length > 0) {
    lines.push('## Debate Transcript', '');
    for (const ev of run.events) {
      if (ev.agent && ev.text) {
        lines.push(`### [${ev.agent}]`, '', formatDocText(ev.text), '');
      }
    }
  }

  return lines.join('\n');
}

/** Wipe store. */
export function wipe(): Promise<void> {
  return idb.deleteDatabase();
}