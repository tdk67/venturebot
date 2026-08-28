/**
 * migrations.ts — Schema versioning & backward-compatible migration pipeline.
 *
 * Requirements:
 * 1. Exports are ALWAYS saved using CURRENT_EXPORT_VERSION in the latest schema format.
 * 2. Every schema version has an explicit converter that migrates data from version N to N+1,
 *    populating default values for any new or modified fields.
 * 3. Imports run through the migration chain sequentially (e.g. V1 -> V2 -> V3) so any past
 *    backup format seamlessly converts to the newest internal model.
 */

import * as idb from './idb';
import type { Idea, IdeaRun } from './idb';

export const CURRENT_EXPORT_FORMAT = 'idealint-ideas';
export const CURRENT_EXPORT_VERSION = 2;

export interface ExportEnvelope {
  format: string;
  version: number;
  exportedAt: string;
  count: number;
  ideas: Idea[];
}

// ── Helper parsing primitives ────────────────────────────────────────

export function parseTimestampMs(val: unknown): number {
  if (typeof val === 'number') {
    // If timestamp is in seconds (e.g. < 1e11), convert to milliseconds
    return val < 10000000000 ? Math.floor(val * 1000) : Math.floor(val);
  }
  if (typeof val === 'string') {
    const parsed = Date.parse(val);
    if (!isNaN(parsed)) return parsed;
    const num = Number(val);
    if (!isNaN(num)) return num < 10000000000 ? Math.floor(num * 1000) : Math.floor(num);
  }
  return Date.now();
}

export function parseMetricScore(val: unknown): number | undefined {
  if (typeof val === 'number') return val;
  if (val && typeof val === 'object' && 'score' in (val as Record<string, unknown>)) {
    const s = (val as { score: unknown }).score;
    if (typeof s === 'number') return s;
    if (typeof s === 'string') {
      const n = Number(s);
      if (!isNaN(n)) return n;
    }
  }
  if (typeof val === 'string') {
    const n = Number(val);
    if (!isNaN(n)) return n;
  }
  return undefined;
}

export function normalizeScoresObj(scores: unknown): { novelty?: number; feasibility?: number; market_fit?: number } | undefined {
  if (!scores || typeof scores !== 'object') return undefined;
  const s = scores as Record<string, unknown>;
  const novelty = parseMetricScore(s.novelty);
  const feasibility = parseMetricScore(s.feasibility);
  const market_fit = parseMetricScore(s.market_fit ?? s.marketFit);
  if (novelty !== undefined || feasibility !== undefined || market_fit !== undefined) {
    return { novelty, feasibility, market_fit };
  }
  return undefined;
}

export function parseTranscriptEvents(raw: unknown): Array<{ agent?: string; text?: string; ts?: number }> {
  if (Array.isArray(raw)) {
    return raw.map((item) => {
      if (item && typeof item === 'object') {
        const o = item as Record<string, unknown>;
        return {
          agent: String(o.agent || o.name || 'Agent'),
          text: String(o.text || o.content || o.message || ''),
          ts: typeof o.ts === 'number' ? parseTimestampMs(o.ts) : undefined,
        };
      }
      return { agent: 'System', text: String(item) };
    });
  }
  if (typeof raw === 'string' && raw.trim().startsWith('[')) {
    try {
      const parsed = JSON.parse(raw);
      return parseTranscriptEvents(parsed);
    } catch {
      // ignore
    }
  }
  return [];
}

// ── Version 1 -> Version 2 Migrator ──────────────────────────────────

/**
 * Migrates a V1 payload (venturebot-ideas-backup / venturebot-ideas / raw unversioned data)
 * to the V2 schema:
 * - Ensures top-level UUIDs exist for all ideas (falling back to runs[0].idea_id or generating uuid).
 * - Converts all second-based timestamps (created_at, updated_at, started_at) to milliseconds (createdAt, updatedAt).
 * - Normalizes nested score objects { novelty: { score: 6 } } into flat numbers { novelty: 6 }.
 * - Deserializes stringified debate_transcript JSON into typed event arrays.
 * - Adds defaults: urls: [], tags: [], status: 'ACTIVE', runs: [].
 */
export function migrateV1ToV2(rawDoc: unknown): ExportEnvelope {
  let rawList: unknown[] = [];
  if (Array.isArray(rawDoc)) {
    rawList = rawDoc;
  } else if (rawDoc && typeof rawDoc === 'object') {
    const doc = rawDoc as Record<string, unknown>;
    if (Array.isArray(doc.ideas)) {
      rawList = doc.ideas;
    } else if (doc.title || doc.pitch) {
      rawList = [doc];
    }
  }

  const ideas: Idea[] = [];

  for (const rawItem of rawList) {
    if (!rawItem || typeof rawItem !== 'object') continue;
    const o = rawItem as Record<string, unknown>;

    const title = String(o.title || o.pitch || '').trim();
    if (!title) continue;

    const rawRuns = Array.isArray(o.runs) ? o.runs : [];
    const runs: IdeaRun[] = rawRuns
      .filter((r): r is Record<string, unknown> => !!r && typeof r === 'object')
      .map((r, idx) => {
        const startedAt = parseTimestampMs(r.started_at ?? r.startedAt ?? r.created_at ?? r.createdAt);
        const finishedAt = (r.finished_at || r.finishedAt) ? parseTimestampMs(r.finished_at ?? r.finishedAt) : undefined;
        const events = parseTranscriptEvents(r.events ?? r.debate_transcript ?? r.transcript);
        const scores = normalizeScoresObj(r.scores);

        let verdict = (r.verdict as string) || '';
        if (!verdict && scores) verdict = 'EVALUATED';

        return {
          run_id: String(r.run_id || r.id || idb.uuid()),
          run_number: typeof r.run_number === 'number' ? r.run_number : idx + 1,
          status: String(r.status || 'DONE'),
          started_at: startedAt,
          finished_at: finishedAt,
          verdict: verdict || undefined,
          verdict_text: typeof r.verdict_text === 'string' ? r.verdict_text : undefined,
          scores,
          prd_text: typeof r.prd_text === 'string' ? r.prd_text : (typeof r.prd === 'string' ? r.prd : undefined),
          research_brief: typeof r.research_brief === 'string' ? r.research_brief : undefined,
          advocate_argument: typeof r.advocate_argument === 'string' ? r.advocate_argument : undefined,
          critic_rebuttal: typeof r.critic_rebuttal === 'string' ? r.critic_rebuttal : undefined,
          creative_angles: typeof r.creative_angles === 'string' ? r.creative_angles : undefined,
          security_audit: (r.security_audit && typeof r.security_audit === 'object') ? (r.security_audit as Record<string, unknown>) : undefined,
          events,
          turns_used: typeof r.turns_used === 'number' ? r.turns_used : undefined,
          comment: typeof r.comment === 'string' ? r.comment : undefined,
        };
      });

    // If no runs were present but top-level debate content exists, synthesize run 1
    if (runs.length === 0 && (o.debate_transcript || o.prd_text || o.research_brief || o.verdict)) {
      const events = parseTranscriptEvents(o.debate_transcript || o.transcript || o.events);
      const scores = normalizeScoresObj(o.scores);
      runs.push({
        run_id: idb.uuid(),
        run_number: 1,
        status: String(o.status || 'DONE'),
        started_at: parseTimestampMs(o.created_at ?? o.createdAt),
        finished_at: parseTimestampMs(o.updated_at ?? o.updatedAt),
        verdict: (o.verdict as string) || (scores ? 'EVALUATED' : undefined),
        verdict_text: typeof o.verdict_text === 'string' ? o.verdict_text : undefined,
        scores,
        prd_text: typeof o.prd_text === 'string' ? o.prd_text : (typeof o.prd === 'string' ? o.prd : undefined),
        research_brief: typeof o.research_brief === 'string' ? o.research_brief : undefined,
        events,
      });
    }

    const createdAt = parseTimestampMs(o.createdAt ?? o.created_at);
    const updatedAt = parseTimestampMs(o.updatedAt ?? o.updated_at);

    let id = String(o.id || '');
    if (!id && rawRuns.length > 0) {
      const firstRunRaw = rawRuns[0] as Record<string, unknown> | undefined;
      if (firstRunRaw?.idea_id) id = String(firstRunRaw.idea_id);
    }
    if (!id) id = idb.uuid();

    const scores = normalizeScoresObj(o.scores) || (runs.length > 0 ? runs[runs.length - 1].scores : undefined);
    const verdict = String(o.verdict || (runs.length > 0 ? runs[runs.length - 1].verdict : '') || '');
    const prd_text = String(o.prd_text || (runs.length > 0 ? runs[runs.length - 1].prd_text : '') || '') || undefined;
    const urls = Array.isArray(o.urls) ? (o.urls as string[]) : [];

    let status = String(o.status || verdict || 'ACTIVE').toUpperCase();
    if (verdict.toUpperCase().includes('PROCEED')) status = 'PROCEED';
    else if (verdict.toUpperCase().includes('PARK')) status = 'PARK';
    else if (verdict.toUpperCase().includes('PRUNE')) status = 'PRUNE';

    ideas.push({
      id,
      title,
      urls,
      status,
      tags: Array.isArray(o.tags) ? (o.tags as string[]) : [],
      createdAt,
      updatedAt,
      runs,
      latestRunId: runs.length > 0 ? runs[runs.length - 1].run_id : undefined,
      prd_text,
      verdict: verdict || undefined,
      scores,
    });
  }

  return {
    format: CURRENT_EXPORT_FORMAT,
    version: 2,
    exportedAt: new Date().toISOString(),
    count: ideas.length,
    ideas,
  };
}

// ── Registry of Migration Steps ──────────────────────────────────────

export type MigrationStep = (data: unknown) => unknown;

/**
 * Migration chain: Each entry converts from version N to version N+1.
 */
export const MIGRATION_CHAIN: Record<number, MigrationStep> = {
  1: migrateV1ToV2,
  // For future schema updates:
  // 2: migrateV2ToV3,
};

/**
 * Detects the schema version of an arbitrary JSON payload.
 */
export function detectDocumentVersion(parsed: unknown): number {
  if (!parsed || typeof parsed !== 'object') return 1;
  const doc = parsed as Record<string, unknown>;

  if (typeof doc.version === 'number') {
    return doc.version;
  }
  // If format is idealint-ideas and version is missing, treat as v2
  if (doc.format === CURRENT_EXPORT_FORMAT) {
    return 2;
  }
  // Otherwise it is legacy v1 (venturebot-ideas-backup, venturebot-ideas, raw array, etc.)
  return 1;
}

/**
 * Main migration runner:
 * Reads any document version and sequentially migrates it to CURRENT_EXPORT_VERSION.
 */
export function migrateToLatest(parsed: unknown): ExportEnvelope {
  const startVersion = detectDocumentVersion(parsed);
  let currentData: unknown = parsed;
  let currentVersion = startVersion;

  while (currentVersion < CURRENT_EXPORT_VERSION) {
    const step = MIGRATION_CHAIN[currentVersion];
    if (!step) {
      throw new Error(`No migration step defined for schema version ${currentVersion}`);
    }
    currentData = step(currentData);
    currentVersion++;
  }

  // If already at or above current version, validate and return normalized envelope
  if (currentData && typeof currentData === 'object') {
    const env = currentData as Partial<ExportEnvelope>;
    if (Array.isArray(env.ideas)) {
      return {
        format: CURRENT_EXPORT_FORMAT,
        version: CURRENT_EXPORT_VERSION,
        exportedAt: typeof env.exportedAt === 'string' ? env.exportedAt : new Date().toISOString(),
        count: env.ideas.length,
        ideas: env.ideas,
      };
    }
  }

  // Fallback to V1->V2 normalization
  return migrateV1ToV2(currentData);
}
