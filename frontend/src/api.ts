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