/**
 * IndexedDB wrapper for Idea Lint (D2).
 *
 * Ideas and settings live ONLY client-side — no server idea table.
 * All functions are typed and promise-based.
 */

export interface IdeaRun {
  run_id: string;
  run_number: number;
  status: string;
  started_at: number;
  finished_at?: number;
  verdict?: string;
  verdict_text?: string;
  scores?: {
    novelty?: number;
    feasibility?: number;
    market_fit?: number;
  };
  prd_text?: string;
  research_brief?: string;
  advocate_argument?: string;
  critic_rebuttal?: string;
  creative_angles?: string;
  security_audit?: Record<string, unknown>;
  events?: Array<{
    agent?: string;
    text?: string;
    event?: string;
    data?: Record<string, unknown>;
    ts?: number;
  }>;
  turns_used?: number;
  comment?: string;
}

export interface Idea {
  /** uuid — stable across export/import so duplicates are detectable. */
  id: string;
  /** Normalized pitch text (trimmed). */
  title: string;
  /** Optional research URLs. */
  urls?: string[];
  /** Idea status: NEW, ACTIVE, PROCEED, PARK, PRUNE. */
  status?: string;
  /** Tags / categories. */
  tags?: string[];
  /** Epoch ms when the idea was first saved. */
  createdAt: number;
  /** Epoch ms of the last mutation. */
  updatedAt: number;
  /** Complete historical runs for this idea. */
  runs?: IdeaRun[];
  /** Shortcut to latest run id. */
  latestRunId?: string;
  /** Latest PRD text. */
  prd_text?: string;
  /** Latest verdict. */
  verdict?: string;
  /** Latest scores. */
  scores?: {
    novelty?: number;
    feasibility?: number;
    market_fit?: number;
  };
}

const DB_NAME = 'venturebot';
const DB_VERSION = 2;
const STORE_IDEAS = 'ideas';
const STORE_SETTINGS = 'settings';
const KEY = 'id';
const IDX_TITLE = 'by_title';

let _dbPromise: Promise<IDBDatabase> | null = null;

function open(): Promise<IDBDatabase> {
  if (_dbPromise) return _dbPromise;
  _dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_IDEAS)) {
        const store = db.createObjectStore(STORE_IDEAS, { keyPath: KEY });
        store.createIndex(IDX_TITLE, 'title', { unique: true });
      }
      if (!db.objectStoreNames.contains(STORE_SETTINGS)) {
        db.createObjectStore(STORE_SETTINGS, { keyPath: 'key' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return _dbPromise;
}

/** Delete the whole database blob (used by tests / clear action). */
export async function deleteDatabase(): Promise<void> {
  if (_dbPromise) {
    const db = await _dbPromise;
    db.close();
    _dbPromise = null;
  }
  await new Promise<void>((resolve, reject) => {
    const req = indexedDB.deleteDatabase(DB_NAME);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
    req.onblocked = () => resolve();
  });
}

function txPromise<T>(
  storeName: string,
  mode: IDBTransactionMode,
  fn: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  return open().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const tx = db.transaction(storeName, mode);
        const store = tx.objectStore(storeName);
        const req = fn(store);
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      }),
  );
}

// ── Ideas store ──────────────────────────────────────────────────────

/** Write an idea (upsert). */
export function putIdea(idea: Idea): Promise<IDBValidKey> {
  return txPromise(STORE_IDEAS, 'readwrite', (s) => s.put(idea));
}

export function getIdea(id: string): Promise<Idea | undefined> {
  return txPromise<IDBValidKey>(STORE_IDEAS, 'readonly', (s) => s.get(id)).then(
    (r) => r as unknown as Idea | undefined,
  );
}

export function getAllIdeas(): Promise<Idea[]> {
  return open().then(
    (db) =>
      new Promise<Idea[]>((resolve, reject) => {
        const tx = db.transaction(STORE_IDEAS, 'readonly');
        const req = tx.objectStore(STORE_IDEAS).getAll();
        req.onsuccess = () =>
          resolve((req.result as Idea[]).sort((a, b) => b.createdAt - a.createdAt));
        req.onerror = () => reject(req.error);
      }),
  );
}

export function deleteIdea(id: string): Promise<void> {
  return open().then(
    (db) =>
      new Promise<void>((resolve, reject) => {
        const tx = db.transaction(STORE_IDEAS, 'readwrite');
        tx.objectStore(STORE_IDEAS).delete(id);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      }),
  );
}

// ── Settings store ───────────────────────────────────────────────────

export interface SettingRecord {
  key: string;
  value: unknown;
  updatedAt: number;
}

export async function getSetting<T = unknown>(key: string): Promise<T | null> {
  try {
    const rec = (await txPromise<SettingRecord | undefined>(
      STORE_SETTINGS,
      'readonly',
      (s) => s.get(key),
    )) as unknown as SettingRecord | undefined;
    return rec ? (rec.value as T) : null;
  } catch {
    return null;
  }
}

export async function setSetting(key: string, value: unknown): Promise<void> {
  try {
    await txPromise(STORE_SETTINGS, 'readwrite', (s) =>
      s.put({ key, value, updatedAt: Date.now() }),
    );
  } catch (err) {
    console.warn('[idb] could not write setting:', err);
  }
}

export async function deleteSetting(key: string): Promise<void> {
  try {
    await txPromise(STORE_SETTINGS, 'readwrite', (s) => s.delete(key));
  } catch {
    // ignore
  }
}

// ── Utility helpers ──────────────────────────────────────────────────

/** Create a fresh, normalized idea record. */
export function newIdea(title: string, urls?: string[]): Idea {
  const now = Date.now();
  return {
    id: uuid(),
    title: title.trim(),
    urls: urls || [],
    status: 'ACTIVE',
    tags: [],
    createdAt: now,
    updatedAt: now,
    runs: [],
  };
}

export function uuid(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}