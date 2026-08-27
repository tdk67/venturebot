/**
 * VentureBot frontend entry (T6). The app shell boots the client-side idea
 * store; later tasks (T7–T9) mount the live debate view and BYOK flow on top.
 */
import { init } from './app-shell';

init();

// Export for test harness access if needed.
(window as unknown as Record<string, unknown>).__venturebot = { init };