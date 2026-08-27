/**
 * VentureBot frontend entry.
 *
 * app.ts is the composition root: it boots the app shell (IndexedDB idea list,
 * T6) and mounts the live debate view (T7) on top. The shell renders one
 * "Run" button per idea; pressing it starts a live per-agent debate.
 */
import * as shell from './app-shell';
import * as debate from './debate';

// BYOK key: T8 owns the key-entry UX and writes the key here. For T7 the
// debate view reads whatever key is present (default to a syntactically valid
// placeholder so a scripted stub debate can run without real credentials).
const KEY_STORAGE = 'vb-api-key';

function currentKey(): string {
  try {
    return window.localStorage.getItem(KEY_STORAGE) ?? 'sk-or-v1-demo-placeholder';
  } catch {
    return 'sk-or-v1-demo-placeholder';
  }
}

shell.init({
  onRun: (idea) => debate.start(idea, currentKey()),
});

// Export for test harness access if needed.
(window as unknown as Record<string, unknown>).__venturebot = { shell, debate };