/**
 * debate.ts — Live debate view (T7).
 *
 * Scope of T7 (REWRITE_PLAN.md Part C):
 *   * per-agent progress chips fed by the SSE stream
 *     (`agent_started` / `agent_finished`, one chip per agent).
 *   * explicit red error banner on `run_failed` — the view must NEVER sit in a
 *     stuck "thinking" state.
 *   * cost/elapsed display (an elapsed timer now; cost is a placeholder until
 *     the real BYOK backend reports token usage in T8).
 *   * a "stop" button per run — stops the local view + closes the SSE stream
 *     (the backend is near-stateless; there is deliberately no kill endpoint).
 *
 * DOM contract (stable ids, exercised by tests/e2e/debate.spec.md):
 *   #debate-run      the live-debate section (hidden until a debate starts)
 *   #debate-state     run-state line (live/done/failed/stopped + elapsed)
 *   #debate-agents    row of per-agent chips
 *   #debate-error     red error banner (hidden unless failed)
 *   #debate-done      green success panel (shows result / PRD link)
 *   #btn-stop         the per-run stop button
 */
import * as api from './api';
import { byId, dom } from './dom';
import type { Idea } from './idb';

export const DEBATE_AGENTS = [
  'Researcher',
  'Advocate',
  'Critic',
  'Creative',
  'Judge',
  'PRD Writer',
  'Security Auditor',
];

type RunState = 'idle' | 'running' | 'done' | 'failed' | 'stopped';

interface AgentChip {
  el: HTMLElement;
  state: 'pending' | 'active' | 'done';
}

interface DebPayload {
  agent?: string;
  duration?: number;
  reason?: string;
  run_id?: string;
  status?: string;
}

interface RunView {
  idea: Idea;
  runId: string;
  state: RunState;
  startedAt: number;
  elapsedTimer: ReturnType<typeof setInterval> | null;
  controller: AbortController | null;
  chips: Map<string, AgentChip>;
}

let view: RunView | null = null;

// -- DOM helpers -----------------------------------------------------------

function setStateLabel(label: string): void {
  const line = byId('debate-state');
  line.dataset.label = label;
  const secs = view ? Math.max(0, Math.floor((Date.now() - view.startedAt) / 1000)) : 0;
  line.textContent = `${label} · ${formatSecs(secs)}`;
}

function formatSecs(secs: number): string {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function renderElapsed(): void {
  if (!view) return;
  const line = byId('debate-state');
  const label = line.dataset.label ?? 'running';
  const secs = Math.max(0, Math.floor((Date.now() - view.startedAt) / 1000));
  line.textContent = `${label} · ${formatSecs(secs)}`;
}

function buildChips(): Map<string, AgentChip> {
  const row = byId('debate-agents');
  row.innerHTML = '';
  const chips = new Map<string, AgentChip>();
  for (const name of DEBATE_AGENTS) {
    const el = dom(
      'span',
      'chip px-3 py-1.5 rounded-full text-xs font-bold bg-slate-800 text-slate-400 border border-slate-700',
      name,
    );
    el.setAttribute('data-agent', name);
    row.appendChild(el);
    chips.set(name, { el, state: 'pending' });
  }
  return chips;
}

function markAgent(name: string, state: 'active' | 'done'): void {
  if (!view) return;
  const chip = view.chips.get(name);
  if (!chip) return;
  chip.state = state;
  chip.el.classList.remove('bg-blue-600', 'text-white', 'bg-emerald-600');
  if (state === 'active') chip.el.classList.add('bg-blue-600', 'text-white');
  else if (state === 'done') chip.el.classList.add('bg-emerald-600', 'text-white');
}

function ensureDoneHidden(): void {
  byId('debate-done').classList.add('hidden');
}
function ensureErrorHidden(): void {
  byId('debate-error').classList.add('hidden');
}

function fail(message: string): void {
  if (!view) return;
  view.state = 'failed';
  stopElapsed();
  setStateLabel('failed');
  const err = byId('debate-error');
  err.textContent = `⚠ ${message}`;
  err.classList.remove('hidden');
  ensureDoneHidden();
  byId('btn-stop').classList.add('hidden');
  markAllNotActive();
}

function markAllNotActive(): void {
  if (!view) return;
  for (const [name, chip] of view.chips) {
    void name;
    if (chip.state === 'active') chip.el.classList.remove('bg-blue-600', 'text-white');
  }
}

function stopElapsed(): void {
  if (view?.elapsedTimer) {
    clearInterval(view.elapsedTimer);
    view.elapsedTimer = null;
  }
}

function showDone(prdNote: string): void {
  if (!view) return;
  view.state = 'done';
  stopElapsed();
  setStateLabel('done');
  ensureErrorHidden();
  const done = byId('debate-done');
  done.classList.remove('hidden');
  byId('debate-done-title').textContent = 'Debate complete — review your PRD';
  byId('debate-prd-link').textContent = prdNote;
  byId('btn-stop').classList.add('hidden');
}

// -- Public API ------------------------------------------------------------

/** Reset the live panel to its empty, idle state. */
export function reset(): void {
  if (view?.controller) view.controller.abort();
  stopElapsed();
  view = null;
  const panel = byId('debate-run');
  panel.classList.add('hidden');
  byId('debate-agents').innerHTML = '';
  setStateLabel('idle');
  ensureErrorHidden();
  ensureDoneHidden();
  byId('btn-stop').classList.add('hidden');
  byId<HTMLButtonElement>('btn-stop').disabled = false;
}

/** Start a live debate run for an idea with a BYOK key (T8 passes it in). */
export function start(idea: Idea, apiKey: string): void {
  reset();
  void run(idea, apiKey);
}

async function run(idea: Idea, apiKey: string): Promise<void> {
  const startedAt = Date.now();
  view = {
    idea,
    runId: '',
    state: 'running',
    startedAt,
    elapsedTimer: null,
    controller: null,
    chips: buildChips(),
  };
  byId('debate-run').classList.remove('hidden');
  const stopBtn = byId<HTMLButtonElement>('btn-stop');
  stopBtn.classList.remove('hidden');
  stopBtn.disabled = false;
  stopBtn.onclick = () => stopRun();

  setStateLabel('starting');
  // Responsive elapsed timer even before the first SSE event.
  view.elapsedTimer = setInterval(renderElapsed, 500);

  let runId: string;
  try {
    runId = await api.createDebate(idea.title, apiKey);
  } catch (err) {
    if (view && view.state === 'running') fail(`Could not start the debate: ${String(err)}`);
    return;
  }
  if (!view || view.state !== 'running') return; // stopped while awaiting
  view.runId = runId;
  setStateLabel('live');
  await stream(runId);
}

function stopRun(): void {
  if (view?.controller) view.controller.abort();
  if (view) {
    view.state = 'stopped';
    stopElapsed();
    setStateLabel('stopped');
  }
  byId('btn-stop').classList.add('hidden');
}

// -- SSE streaming ---------------------------------------------------------

function stream(runId: string): Promise<void> {
  return new Promise((resolve) => {
    if (!view || view.state !== 'running') return resolve();
    const es = new EventSource(`/api/debates/${encodeURIComponent(runId)}/events`);
    const controller = new AbortController();
    view.controller = controller;

    const settle = () => {
      es.close();
      if (view) {
        stopElapsed();
        // keep state/labels; we have already resolved a terminal event
      }
      resolve();
    };

    const onEvent = (name: string) => (e: MessageEvent) => {
      let payload: DebPayload = {};
      try {
        payload = JSON.parse(e.data) as DebPayload;
      } catch {
        payload = {};
      }
      handle(name, payload);
    };

    es.addEventListener('agent_started', onEvent('agent_started'));
    es.addEventListener('agent_finished', onEvent('agent_finished'));
    es.addEventListener('run_finished', onEvent('run_finished'));
    es.addEventListener('run_failed', onEvent('run_failed'));
    es.addEventListener('expired', onEvent('expired'));

    es.onerror = () => {
      // Stream dropped. If we know the run finished server-side, probe the
      // result endpoint; otherwise fail loudly (never a silent hang).
      if (view && view.state === 'running') {
        void probe(runId);
      }
    };

    const handle = (name: string, payload: DebPayload): void => {
      if (!view) return;
      if (name === 'agent_started') {
        if (payload.agent) markAgent(payload.agent, 'active');
        setStateLabel('running');
      } else if (name === 'agent_finished') {
        if (payload.agent) markAgent(payload.agent, 'done');
      } else if (name === 'run_finished') {
        showDone('PRD ready — click to download');
        settle();
      } else if (name === 'run_failed') {
        fail(payload.reason ?? 'run failed');
        settle();
      } else if (name === 'expired') {
        fail('run expired on the server');
        settle();
      }
    };

    async function probe(runIdProbe: string): Promise<void> {
      try {
        const result = await api.fetchResult(runIdProbe, 4000);
        if (result) {
          showDone('PRD ready — click to download');
          settle();
          return;
        }
      } catch {
        // fall through to a loud failure below
      }
      if (view && view.state === 'running') {
        fail('live stream disconnected (network). Run may still be in progress.');
        settle();
      }
    }
  });
}