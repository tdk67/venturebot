/**
 * Minimal promise wrapper around IndexedDB for the idea store.
 *
 * Decision model (D2): ideas live ONLY client-side — no server idea table.
 * This module is the single persistence seam for T6.
 */

export interface Idea {
  /** uuid — stable across export/import so duplicates are detectable. */
  id: string;
  /** Normalized pitch text (trimmed). */
  title: string;
  /** Epoch ms when the idea was first saved. */
  createdAt: number;
  /** Epoch ms of the last mutation. */
  updatedAt: number;
}

const DB_NAME = 'venturebot';
const DB_VERSION = 1;
const STORE = 'ideas';
const KEY = 'id';
const IDX_TITLE = 'by_title';

let _dbPromise: Promise<IDBDatabase> | null = null;

function open(): Promise<IDBDatabase> {
  if (_dbPromise) return _dbPromise;
  _dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: KEY });
        store.createIndex(IDX_TITLE, 'title', { unique: true });
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
    req.onblocked = () => resolve(); // another tab holds it; ignore for our use
  });
}

function txPromise<T>(mode: IDBTransactionMode, fn: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return open().then((db) =>
    new Promise<T>((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const store = tx.objectStore(STORE);
      const req = fn(store);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    }),
  );
}

/** Write an idea. In the id-keyed store this is put-or-update (upsert). */
export function putIdea(idea: Idea): Promise<IDBValidKey> {
  return txPromise('readwrite', (s) => s.put(idea));
}

export function getIdea(id: string): Promise<Idea | undefined> {
  return txPromise<IDBValidKey>('readonly', (s) => s.get(id)).then((r) => r as unknown as Idea | undefined);
}

export function getAllIdeas(): Promise<Idea[]> {
  return open().then(
    (db) =>
      new Promise<Idea[]>((resolve, reject) => {
        const tx = db.transaction(STORE, 'readonly');
        const req = tx.objectStore(STORE).getAll();
        req.onsuccess = () => resolve((req.result as Idea[]).sort((a, b) => b.createdAt - a.createdAt));
        req.onerror = () => reject(req.error);
      }),
  );
}

export function deleteIdea(id: string): Promise<void> {
  return open().then(
    (db) =>
      new Promise<void>((resolve, reject) => {
        const tx = db.transaction(STORE, 'readwrite');
        tx.objectStore(STORE).delete(id);
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      }),
  );
}

/** Create a fresh, normalized idea record. */
export function newIdea(title: string): Idea {
  const now = Date.now();
  return { id: uuid(), title: title.trim(), createdAt: now, updatedAt: now };
}

export function uuid(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  // Fallback for environments without crypto.randomUUID.
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}