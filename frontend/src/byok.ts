/**
 * byok.ts — BYOK key UX (T8).
 *
 * Scope of T8 (REWRITE_PLAN.md Part C):
 *   * key entry form on the app page (never star/echo the full key back),
 *   * call `/api/byok/verify` BEFORE the first run so a wrong key blocks the
 *     run with an explicit message instead of a mid-debate failure,
 *   * store the key in localStorage, and NEVER render it back to the page,
 *   * the key is only ever sent to create-run and verify endpoints.
 *
 * The gateway contract: `hasValidatedKey()` returns true only once a key has
 * been verified against the backend. `startRequiresKey()` is consumed by the
 * composition root (`app.ts`) to block the Run button until a valid key is
 * present, with a human-readable reason. The raw key never touches the DOM.
 */

import * as api from './api';

const KEY_STORAGE = 'vb-api-key';

export interface KeyState {
  saved: boolean;      // a key string exists in localStorage
  validated: boolean;  // the stored key passed /api/byok/verify this session
  provider?: string;
}

// Latest known state. `validated` is intentionally NOT persisted: re-verifying
// on each load keeps us honest if the user swaps the key in another tab.
let cached: KeyState | null = null;

function readStored(): string | null | undefined {
  try {
    return window.localStorage.getItem(KEY_STORAGE);
  } catch {
    return undefined; // storage unavailable → treat as no saved key
  }
}

/**
 * Best-effort read of the stored key (no verification). Returns null when no
 * key is stored. The caller MAY pass it to create-debate/verify but must never
 * render it back to the DOM.
 */
export function storedKey(): string | null {
  const k = readStored();
  return typeof k === 'string' && k.length > 0 ? k : null;
}

function setStored(key: string | null): void {
  try {
    if (key === null) window.localStorage.removeItem(KEY_STORAGE);
    else window.localStorage.setItem(KEY_STORAGE, key);
  } catch {
    // storage blocked (private mode); in-memory session still works
  }
}

/** Current key state — recomputed cheaply from localStorage. */
export function keyState(): KeyState {
  const k = readStored();
  return {
    saved: typeof k === 'string' && k.length > 0,
    validated: cached?.validated === true && cached?.saved === true,
    provider: cached?.provider,
  };
}

/**
 * Persist a key and (if valid) remember it as validated. On any invalid key we
 * clear the validated flag but still store nothing for an empty value.
 */
export function saveKey(key: string): void {
  const trimmed = key.trim();
  if (!trimmed) {
    clearKey();
    return;
  }
  setStored(trimmed);
  cached = { saved: true, validated: false };
}

/** Remove the stored key and any validation state. */
export function clearKey(): void {
  setStored(null);
  cached = null;
}

/**
 * Submit the given key to `/api/byok/verify`. On success stores it and flags
 * validated; on failure leaves the key stored but not validated and returns a
 * human message for the UI. The raw key is NEVER returned.
 */
export async function verify(key: string): Promise<{ ok: boolean; message: string; provider?: string }> {
  const trimmed = key.trim();
  if (!trimmed) {
    return { ok: false, message: 'Please paste your API key first.' };
  }
  let res: api.ByokVerifyResponse;
  try {
    res = await api.verifyByokKey(trimmed);
  } catch (err) {
    return { ok: false, message: `Could not reach the verify endpoint: ${String(err)}` };
  }
  if (res.valid) {
    saveKey(trimmed);
    cached = { saved: true, validated: true, provider: res.provider };
    return { ok: true, message: `Valid ${res.provider ?? 'API'} key — saved in this browser only.`, provider: res.provider };
  }
  // Keep nothing: a clearly invalid key should not be stored.
  clearKey();
  return {
    ok: false,
    message: 'That key looks invalid. It is used only for this debate — double-check it and try again.',
  };
}

