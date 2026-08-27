/**
 * VentureBot frontend entry (composition root).
 *
 * Boots the app shell (IndexedDB idea list, T6), mounts the live debate view
 * (T7), and wires the BYOK key UX (T8): the user's key is stored in
 * localStorage only, verified via /api/byok/verify before the first run, and
 * never rendered back to the page. A run is blocked with a clear message until
 * a key is present.
 *
 * Key-privacy contract (T8): the platform root is the ONLY place that reads
 * the stored key from localStorage and hands it to `debate.start()`. The key
 * is never written into the DOM, never logged, and never sent anywhere except
 * create-debate and verify.
 */
import * as shell from './app-shell';
import * as debate from './debate';
import * as byok from './byok';
import { byId } from './dom';
import type { Idea } from './idb';

// -- key entry UI ----------------------------------------------------------

function setKeyStatus(state: string, text: string): void {
  const el = byId('key-status');
  el.dataset.state = state;
  el.textContent = text;
}

function refreshKeyUi(): void {
  const st = byok.keyState();
  const clear = byId('btn-key-clear');
  clear.classList.toggle('hidden', !st.saved);
  const label = st.saved && st.validated ? 'key set' : st.saved ? 'key saved (unverified)' : 'unknown';
  setKeyStatus(label, st.saved ? 'API key saved in this browser only.' : 'No key set yet. Open the form to add one.');
}

function wireKeyUi(): void {
  const btnOpen = byId('btn-key-open');
  const btnClear = byId('btn-key-clear');
  const form = byId('key-form');
  const hint = byId('key-hint');
  const input = byId<HTMLInputElement>('key-input');

  btnOpen.addEventListener('click', () => {
    form.classList.toggle('hidden');
    if (!form.classList.contains('hidden')) input.focus();
  });

  byId('btn-key-verify').addEventListener('click', () => {
    void (async () => {
      hint.classList.add('hidden');
      const value = input.value;
      if (!value.trim()) {
        hint.textContent = 'Please paste your API key first.';
        hint.classList.remove('hidden');
        return;
      }
      const out = await byok.verify(value);
      input.value = ''; // never echo the pasted value back into the DOM
      hint.textContent = out.message;
      hint.classList.remove('hidden');
      refreshKeyUi();
    })();
  });

  btnClear.addEventListener('click', () => {
    byok.clearKey();
    input.value = '';
    hint.classList.add('hidden');
    form.classList.add('hidden');
    refreshKeyUi();
  });

  refreshKeyUi();
}

// -- run gating (T8) -------------------------------------------------------

function onRun(idea: Idea): void {
  const status = byId('status-line');
  const blocked = byok.keyState().saved && !byok.keyState().validated;
  if (blocked) {
    status.textContent =
      'Your key is saved but not verified yet. Open the key form above and press "Verify & save" first.';
    return;
  }
  const key = byok.storedKey();
  if (key === null) {
    status.textContent = 'Set your model API key first (via the key button above).';
    return;
  }
  debate.start(idea, key);
}

// -- boot ------------------------------------------------------------------

shell.init({ onRun });
wireKeyUi();

// Export for test harness access if needed.
(window as unknown as Record<string, unknown>).__venturebot = { shell, debate, byok };