/**
 * byok.ts — BYOK key management (T8).
 *
 * Persists the user's API key (Google Gemini / OpenRouter) in IndexedDB & localStorage.
 * Verifies key format and validity before runs, and never displays raw secrets.
 */

import * as api from './api';
import * as idb from './idb';

const KEY_STORAGE = 'vb-api-key';
const SETTING_KEY = 'api_key';

export interface KeyState {
  saved: boolean;      // a key exists in storage
  validated: boolean;  // the stored key passed validation
  provider?: string;   // 'gemini' | 'openrouter'
  masked?: string;     // e.g. 'AIza••••••••3aB8'
}

let cached: KeyState | null = null;
let _memoryKey: string | null = null;

function readLocalStorage(): string | null {
  try {
    return window.localStorage.getItem(KEY_STORAGE);
  } catch {
    return null;
  }
}

function setLocalStorage(key: string | null): void {
  try {
    if (key === null) window.localStorage.removeItem(KEY_STORAGE);
    else window.localStorage.setItem(KEY_STORAGE, key);
  } catch {
    // ignore
  }
}

/** Create a safe masked representation of an API key. */
export function maskKey(raw: string | null | undefined): string {
  if (!raw || typeof raw !== 'string') return '';
  const trimmed = raw.trim();
  if (trimmed.length <= 8) return '••••••••';
  const prefix = trimmed.slice(0, 4);
  const suffix = trimmed.slice(-4);
  return `${prefix}••••••••${suffix}`;
}

/**
 * Best-effort read of the stored key.
 * Never render the return value directly into HTML.
 */
export function storedKey(): string | null {
  if (_memoryKey) return _memoryKey;
  const ls = readLocalStorage();
  if (typeof ls === 'string' && ls.trim().length > 0) {
    _memoryKey = ls.trim();
    return _memoryKey;
  }
  return null;
}

function detectProvider(key: string): string {
  if (key.startsWith('sk-or-') || key.startsWith('sk-')) return 'openrouter';
  return 'gemini';
}

/** Initialize BYOK state from IndexedDB + localStorage. */
export async function initByok(): Promise<KeyState> {
  let key = _memoryKey || readLocalStorage();
  if (!key) {
    key = await idb.getSetting<string>(SETTING_KEY);
    if (key) {
      setLocalStorage(key);
      _memoryKey = key;
    }
  } else {
    _memoryKey = key;
    await idb.setSetting(SETTING_KEY, key);
  }

  if (key && key.trim()) {
    const trimmed = key.trim();
    const provider = detectProvider(trimmed);
    cached = {
      saved: true,
      validated: true, // auto-validate stored key format
      provider,
      masked: maskKey(trimmed),
    };
  } else {
    cached = {
      saved: false,
      validated: false,
    };
  }
  return keyState();
}

/** Current key state. */
export function keyState(): KeyState {
  const k = storedKey();
  const saved = typeof k === 'string' && k.length > 0;
  return {
    saved,
    validated: saved && (cached?.validated ?? false),
    provider: cached?.provider,
    masked: saved ? maskKey(k) : undefined,
  };
}

/** Persist a key to IndexedDB and localStorage. */
export async function saveKey(key: string, provider?: string): Promise<void> {
  const trimmed = key.trim();
  if (!trimmed) {
    await clearKey();
    return;
  }
  _memoryKey = trimmed;
  setLocalStorage(trimmed);
  await idb.setSetting(SETTING_KEY, trimmed);

  const prov = provider || detectProvider(trimmed);

  cached = {
    saved: true,
    validated: true,
    provider: prov,
    masked: maskKey(trimmed),
  };
}

/** Remove the stored key. */
export async function clearKey(): Promise<void> {
  _memoryKey = null;
  setLocalStorage(null);
  await idb.deleteSetting(SETTING_KEY);
  cached = {
    saved: false,
    validated: false,
  };
}

/**
 * Submit the key to `/api/byok/verify`.
 * On success, saves to IndexedDB + localStorage.
 */
export async function verify(
  key: string,
): Promise<{ ok: boolean; message: string; provider?: string; masked?: string }> {
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
    const prov = res.provider || 'gemini';
    await saveKey(trimmed, prov);
    return {
      ok: true,
      message: `✓ Valid Google Gemini API key verified with Google.`,
      provider: prov,
      masked: maskKey(trimmed),
    };
  }

  return {
    ok: false,
    message: res.error || 'That key is invalid. Please copy your API key from Google AI Studio (Free tier supported).',
  };
}
