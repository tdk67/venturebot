/** Types mirroring the near-stateless backend contract (T1). */

export interface CreateDebateRequest {
  idea: string;
  api_key: string;
  urls?: string[];
}

export interface CreateDebateResponse {
  run_id: string;
}

export interface RunStatus {
  run_id: string;
  status: string;
  error?: string;
}

export interface RunResult {
  run_id: string;
  status: string;
  result?: Record<string, unknown>;
  error?: string;
}

export interface ByokVerifyRequest {
  api_key: string;
}

export interface ByokVerifyResponse {
  valid: boolean;
  provider?: string;
  error?: string;
}

export interface IdeaSubmitResponse {
  idea_id: string;
  duplicate: boolean;
}

export const API = {
  createDebate: '/api/debates',
  byokVerify: '/api/byok/verify',
  health: '/api/health',
};

/** Convert a run_id (or idea_id) into a stable IndexedDB key. */
export function runKey(runId: string): string {
  return `run:${runId}`;
}

// -- Live debate client (T7) ---------------------------------------------

const DEFAULT_TIMEOUT_MS = 10_000;

async function _post(path: string, body: unknown): Promise<unknown> {
  const ctrl: AbortController | null = 'AbortController' in window ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), DEFAULT_TIMEOUT_MS) : 0;
  try {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: ctrl?.signal,
    });
    const text = await res.text();
    const data = text ? (JSON.parse(text) as unknown) : null;
    if (!res.ok) {
      const detail =
        data && typeof data === 'object' && 'detail' in data && typeof (data as { detail: unknown }).detail === 'string'
          ? (data as { detail: string }).detail
          : `HTTP ${res.status}`;
      throw new Error(detail);
    }
    return data;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/** Create a debate run. Returns the run_id. */
export async function createDebate(idea: string, apiKey: string): Promise<string> {
  const data = (await _post(API.createDebate, { idea, api_key: apiKey })) as CreateDebateResponse;
  if (!data.run_id) throw new Error('create-debate returned no run_id');
  return data.run_id;
}

/** Fetch the finished result. Throws on not-ready (202) / gone (410) / 404. */
export async function fetchResult(runId: string, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<RunResult | null> {
  const ctrl = 'AbortController' in window ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : 0;
  try {
    const res = await fetch(`/api/debates/${encodeURIComponent(runId)}/result`, { signal: ctrl?.signal });
    if (res.status === 202) return null; // not ready yet
    if (res.status === 404) throw new Error('run not found');
    if (res.status === 410) throw new Error('result gone (expired or already downloaded)');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return (await res.json()) as RunResult;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/** ACK the download so the server can wipe the ephemeral result (S7/D3). */
export async function ackResult(runId: string): Promise<void> {
  await _post(`/api/debates/${encodeURIComponent(runId)}/result/ack`, {});
}

/** Minimal inline parser for a couple of SSE frames (event + data lines). */
export interface SseFrame {
  event: string;
  data: string;
}

export function parseSse(buffer: string): SseFrame[] {
  const frames: SseFrame[] = [];
  let event = 'message';
  const dataLines: string[] = [];
  for (const line of buffer.split(/\r?\n/)) {
    if (line === '') {
      if (dataLines.length) frames.push({ event, data: dataLines.join('\n') });
      event = 'message';
      dataLines.length = 0;
      continue;
    }
    if (line.startsWith(':')) continue; // comment / keep-alive
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
  }
  // Flush any trailing partial frame (no blank line yet).
  if (dataLines.length) frames.push({ event, data: dataLines.join('\n') });
  return frames;
}