/**
 * debate.ts — Live debate runner & streaming client for Idea Lint (T7/T8).
 */
import * as api from './api';
import * as store from './store';
import { byId, dom } from './dom';
import type { Idea, IdeaRun } from './idb';

declare global {
  interface Window {
    marked?: {
      parse: (md: string) => string;
    };
    DOMPurify?: {
      sanitize: (html: string, options?: unknown) => string;
    };
  }
}

export const DEBATE_AGENTS = [
  'Researcher',
  'Advocate',
  'Critic',
  'Creative',
  'Judge',
  'PRD Writer',
  'Security Auditor',
];

const AGENT_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  Researcher: { bg: 'bg-cyan-950/60', text: 'text-cyan-400', border: 'border-cyan-700/60' },
  Advocate: { bg: 'bg-emerald-950/60', text: 'text-emerald-400', border: 'border-emerald-700/60' },
  Critic: { bg: 'bg-rose-950/60', text: 'text-rose-400', border: 'border-rose-700/60' },
  Creative: { bg: 'bg-purple-950/60', text: 'text-purple-400', border: 'border-purple-700/60' },
  Judge: { bg: 'bg-amber-950/60', text: 'text-amber-400', border: 'border-amber-700/60' },
  'PRD Writer': { bg: 'bg-blue-950/60', text: 'text-blue-400', border: 'border-blue-700/60' },
  'Security Auditor': { bg: 'bg-red-950/60', text: 'text-red-400', border: 'border-red-700/60' },
  System: { bg: 'bg-slate-900', text: 'text-slate-400', border: 'border-slate-700' },
  Human: { bg: 'bg-indigo-950/60', text: 'text-indigo-400', border: 'border-indigo-700/60' },
};

type RunState = 'idle' | 'running' | 'done' | 'failed' | 'stopped';

interface AgentChip {
  el: HTMLElement;
  state: 'pending' | 'active' | 'done';
}

interface DebPayload {
  agent?: string;
  phase?: string;
  duration?: number;
  reason?: string;
  run_id?: string;
  status?: string;
  text?: string;
  scores?: { novelty?: number; feasibility?: number; market_fit?: number };
  verdict?: string;
  verdict_text?: string;
  key_risks?: string[];
  question?: string;
  answer?: string;
}

interface RunView {
  idea: Idea;
  runId: string;
  state: RunState;
  startedAt: number;
  elapsedTimer: ReturnType<typeof setInterval> | null;
  controller: AbortController | null;
  chips: Map<string, AgentChip>;
  events: Array<{ agent: string; text: string; ts: number }>;
  onFinish?: (idea: Idea) => void;
}

let view: RunView | null = null;

// ── Markdown & JSON rendering helper ────────────────────────────────

export function looksLikeJson(text: string): boolean {
  if (!text || typeof text !== 'string') return false;
  const t = text.trim();
  return (t.startsWith('{') && t.endsWith('}')) || (t.startsWith('[') && t.endsWith(']'));
}

export function jsonToMarkdown(obj: unknown, level = 2): string {
  const human = (k: string) =>
    String(k)
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase());
  const heading = (n: number) => '#'.repeat(Math.min(Math.max(n, 2), 6));
  const clean = (v: unknown) => v !== null && v !== undefined && v !== '';

  if (!clean(obj)) return '';
  if (typeof obj !== 'object') return String(obj);

  if (Array.isArray(obj)) {
    if (!obj.length) return '';
    return obj
      .map((item) => {
        if (item && typeof item === 'object') {
          const parts = Object.entries(item as Record<string, unknown>)
            .filter(([, v]) => clean(v))
            .map(([k, v]) => {
              if (typeof v === 'object') return null;
              if (typeof v === 'string' && v.startsWith('http')) {
                return `**${human(k)}:** [${v}](${v})`;
              }
              return `**${human(k)}:** ${v}`;
            })
            .filter(Boolean);
          const nested = Object.entries(item as Record<string, unknown>)
            .filter(([, v]) => v && typeof v === 'object' && clean(v))
            .map(([, v]) => '\n  - ' + String(jsonToMarkdown(v, level + 1)).split('\n').join('\n    '))
            .join('');
          return `- ${parts.join(' — ')}${nested}`;
        }
        if (typeof item === 'string' && item.startsWith('http')) {
          return `- [${item}](${item})`;
        }
        return `- ${item}`;
      })
      .filter((l) => l !== '- ')
      .join('\n');
  }

  return Object.entries(obj as Record<string, unknown>)
    .map(([k, v]) => {
      if (!clean(v)) return '';
      if (typeof v === 'object') {
        return `${heading(level)} ${human(k)}\n\n${jsonToMarkdown(v, level + 1)}`;
      }
      if (typeof v === 'string' && v.startsWith('http')) {
        return `- **${human(k)}:** [${v}](${v})`;
      }
      return `- **${human(k)}:** ${v}`;
    })
    .filter(Boolean)
    .join('\n\n');
}

export function formatDocText(text: unknown): string {
  if (text === null || text === undefined) return '';
  if (typeof text === 'object') {
    return jsonToMarkdown(text);
  }
  const t = String(text).trim();
  if (!looksLikeJson(t)) return t;
  try {
    const parsed = JSON.parse(t);
    return jsonToMarkdown(parsed) || t;
  } catch {
    return t;
  }
}

export function renderMarkdown(raw: unknown): string {
  if (!raw) return '';
  const formatted = formatDocText(raw);
  if (window.marked && window.DOMPurify) {
    try {
      const parsed = window.marked.parse(formatted);
      return window.DOMPurify.sanitize(parsed, {
        ALLOWED_ATTR: ['href', 'title', 'alt', 'target', 'rel'],
      });
    } catch {
      // fallback to text
    }
  }
  const esc = formatted
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  return `<div class="whitespace-pre-wrap font-sans">${esc}</div>`;
}

// ── DOM Helpers ──────────────────────────────────────────────────────

function setStateLabel(label: string): void {
  const line = document.getElementById('debate-state');
  if (!line) return;
  line.dataset.label = label;
  const secs = view ? Math.max(0, Math.floor((Date.now() - view.startedAt) / 1000)) : 0;
  line.textContent = `${label.toUpperCase()} · ${formatSecs(secs)}`;
}

function formatSecs(secs: number): string {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function renderElapsed(): void {
  if (!view) return;
  const line = document.getElementById('debate-state');
  if (!line) return;
  const label = line.dataset.label ?? 'running';
  const secs = Math.max(0, Math.floor((Date.now() - view.startedAt) / 1000));
  line.textContent = `${label.toUpperCase()} · ${formatSecs(secs)}`;
}

function buildChips(): Map<string, AgentChip> {
  const row = document.getElementById('debate-agents');
  if (!row) return new Map();
  row.innerHTML = '';
  const chips = new Map<string, AgentChip>();
  for (const name of DEBATE_AGENTS) {
    const el = dom(
      'span',
      'chip px-3 py-1.5 rounded-full text-xs font-semibold bg-slate-900/90 text-slate-400 border border-slate-700/60 transition-all flex items-center gap-1.5',
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
  chip.el.classList.remove('bg-blue-600', 'text-white', 'border-blue-400', 'bg-emerald-800/90', 'border-emerald-500', 'animate-pulse');
  if (state === 'active') {
    chip.el.innerHTML = `<span class="inline-block w-2 h-2 rounded-full bg-blue-300 animate-ping"></span> <span>${name}</span>`;
    chip.el.classList.add('bg-blue-600', 'text-white', 'border-blue-400');
  } else if (state === 'done') {
    chip.el.innerHTML = `<span class="text-emerald-300 font-bold">✓</span> <span>${name}</span>`;
    chip.el.classList.add('bg-emerald-800/90', 'text-white', 'border-emerald-500');
  }
}

export function appendFeedMessage(agent: string, text: string): void {
  const feed = document.getElementById('debate-feed');
  if (!feed || !text) return;

  const style = AGENT_COLORS[agent] || AGENT_COLORS.System;
  const card = dom('div', `msg p-4 rounded-xl border ${style.bg} ${style.border} text-slate-100 shadow-sm transition-all`);

  const header = dom('div', 'flex items-center justify-between mb-2');
  const tag = dom('span', `text-xs font-bold uppercase tracking-wider ${style.text}`, agent);
  const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const timeEl = dom('span', 'text-[11px] text-slate-500 font-mono', timeStr);
  header.appendChild(tag);
  header.appendChild(timeEl);
  card.appendChild(header);

  const body = dom('div', 'md-body text-sm text-slate-200 leading-relaxed');
  body.innerHTML = renderMarkdown(text);
  card.appendChild(body);

  feed.appendChild(card);
  feed.scrollTop = feed.scrollHeight;

  if (view) {
    view.events.push({ agent, text, ts: Date.now() });
  }
}

function ensureDoneHidden(): void {
  document.getElementById('debate-done')?.classList.add('hidden');
}
function ensureErrorHidden(): void {
  document.getElementById('debate-error')?.classList.add('hidden');
}

function fail(message: string): void {
  if (!view) return;
  view.state = 'failed';
  stopElapsed();
  setStateLabel('failed');
  const err = document.getElementById('debate-error');
  if (err) {
    err.innerHTML = `<div class="font-bold flex items-center gap-2 mb-1"><span class="text-rose-400">❌ Debate Execution Error</span></div><div class="text-xs text-rose-300 font-mono whitespace-pre-wrap leading-relaxed">${message}</div>`;
    err.classList.remove('hidden');
    err.scrollIntoView({ behavior: 'smooth' });
  }
  ensureDoneHidden();
  document.getElementById('btn-stop')?.classList.add('hidden');
  markAllNotActive();
  appendFeedMessage('System', `❌ Error: ${message}`);
}

function markAllNotActive(): void {
  if (!view) return;
  for (const [, chip] of view.chips) {
    if (chip.state === 'active') {
      chip.el.classList.remove('bg-blue-600', 'text-white', 'border-blue-400', 'animate-pulse');
    }
  }
}

function stopElapsed(): void {
  if (view?.elapsedTimer) {
    clearInterval(view.elapsedTimer);
    view.elapsedTimer = null;
  }
}

function extractScoreNumber(raw: unknown): string | number {
  if (raw === null || raw === undefined) return '—';
  if (typeof raw === 'number') return raw;
  if (typeof raw === 'string') {
    const parsed = parseFloat(raw);
    return isNaN(parsed) ? raw : parsed;
  }
  if (typeof raw === 'object' && raw !== null) {
    const s = (raw as Record<string, unknown>).score;
    if (typeof s === 'number') return s;
    if (typeof s === 'string') {
      const parsed = parseFloat(s);
      return isNaN(parsed) ? s : parsed;
    }
  }
  return '—';
}

function renderVerdictGate(payload: DebPayload): void {
  const card = document.getElementById('verdict-card');
  if (!card) return;
  card.classList.remove('hidden');

  const scoresContainer = document.getElementById('verdict-scores');
  if (scoresContainer) {
    scoresContainer.innerHTML = '';
    const sc = (payload.scores || {}) as Record<string, unknown>;
    const metrics: Array<[string, unknown, string]> = [
      ['Novelty', sc.novelty, 'from-purple-600 to-indigo-600'],
      ['Feasibility', sc.feasibility, 'from-blue-600 to-cyan-600'],
      ['Market Fit', sc.market_fit, 'from-emerald-600 to-teal-600'],
    ];

    for (const [name, rawScore, grad] of metrics) {
      const scoreNum = extractScoreNumber(rawScore);
      const col = dom('div', 'bg-slate-900/90 border border-slate-800 p-3 rounded-xl text-center');
      col.appendChild(dom('div', 'text-xs text-slate-400 uppercase font-bold tracking-wider mb-1', name));
      col.appendChild(dom('div', `text-2xl font-black bg-gradient-to-r ${grad} bg-clip-text text-transparent`, `${scoreNum}/10`));
      scoresContainer.appendChild(col);
    }
  }

  const rat = document.getElementById('verdict-rationale');
  if (rat) {
    rat.innerHTML = renderMarkdown(payload.verdict_text || payload.text || '');
  }

  const risks = document.getElementById('verdict-key-risks');
  if (risks && payload.key_risks && payload.key_risks.length > 0) {
    risks.innerHTML = `<span class="font-bold text-amber-400">Key Risks:</span> ${payload.key_risks.join(' · ')}`;
    risks.classList.remove('hidden');
  }

  card.scrollIntoView({ behavior: 'smooth' });
}

// ── PRD Approval UI ──────────────────────────────────────────────────

function renderPrdCard(prdText: string, audit?: Record<string, unknown>): void {
  const card = document.getElementById('prd-card');
  if (!card) return;
  card.classList.remove('hidden');

  const prdBody = document.getElementById('prd-text');
  if (prdBody) {
    prdBody.innerHTML = renderMarkdown(prdText);
  }

  const badge = document.getElementById('audit-badge');
  if (badge) {
    const passed = audit?.status === 'PASS' || !audit?.findings;
    badge.innerHTML = passed
      ? `<span class="px-3 py-1 rounded-full text-xs font-bold bg-emerald-900/80 text-emerald-300 border border-emerald-600">✓ Security Audit: Passed</span>`
      : `<span class="px-3 py-1 rounded-full text-xs font-bold bg-amber-900/80 text-amber-300 border border-amber-600">⚠ Security Audit: Flagged Issues</span>`;
  }

  card.scrollIntoView({ behavior: 'smooth' });
}

// ── Public API ───────────────────────────────────────────────────────

export function reset(): void {
  if (view?.controller) view.controller.abort();
  stopElapsed();
  view = null;

  document.getElementById('debate-run')?.classList.add('hidden');
  document.getElementById('verdict-card')?.classList.add('hidden');
  document.getElementById('prd-card')?.classList.add('hidden');
  document.getElementById('clarify-box')?.classList.add('hidden');
  const feed = document.getElementById('debate-feed');
  if (feed) feed.innerHTML = '';
  const agents = document.getElementById('debate-agents');
  if (agents) agents.innerHTML = '';

  setStateLabel('idle');
  ensureErrorHidden();
  ensureDoneHidden();
}

export function start(
  idea: Idea,
  apiKey: string,
  options?: { urls?: string[]; comment?: string; onFinish?: (idea: Idea) => void },
): void {
  void startRun(idea, apiKey, options);
}

export async function startRun(
  idea: Idea,
  apiKey: string,
  options?: { urls?: string[]; comment?: string; onFinish?: (idea: Idea) => void },
): Promise<void> {
  reset();

  const sec = document.getElementById('debate-run');
  if (sec) {
    sec.classList.remove('hidden');
    sec.scrollIntoView({ behavior: 'smooth' });
  }

  const stopBtn = document.getElementById('btn-stop');
  if (stopBtn) stopBtn.classList.remove('hidden');

  const chips = buildChips();

  view = {
    idea,
    runId: '',
    state: 'running',
    startedAt: Date.now(),
    elapsedTimer: null,
    controller: null,
    chips,
    events: [],
    onFinish: options?.onFinish,
  };

  setStateLabel('starting');
  view.elapsedTimer = setInterval(renderElapsed, 500);

  appendFeedMessage('System', `Initiating multi-agent debate for: "${idea.title}"...`);

  let runId: string;
  try {
    runId = await api.createDebate(idea.title, apiKey, options?.urls || idea.urls, options?.comment);
  } catch (err) {
    if (view && view.state === 'running') {
      fail(`Could not start debate: ${String(err)}`);
    }
    return;
  }

  if (!view || view.state !== 'running') return;
  view.runId = runId;
  setStateLabel('live');
  appendFeedMessage('System', `Debate session active (Run ID: ${runId.slice(0, 8)}). Engaging agents...`);

  await stream(runId);
}

export function stopRun(): void {
  if (view?.controller) view.controller.abort();
  if (view) {
    const runId = view.runId;
    view.state = 'stopped';
    stopElapsed();
    setStateLabel('stopped');
    appendFeedMessage('System', 'Debate stopped by user.');
    if (runId) {
      void api.stopDebate(runId, currentApiKey);
    }
  }
  document.getElementById('btn-stop')?.classList.add('hidden');
}

// ── Polling & Result Handling ────────────────────────────────────────

function stream(runId: string): Promise<void> {
  return new Promise((resolve) => {
    if (!view || view.state !== 'running') return resolve();

    const controller = new AbortController();
    view.controller = controller;

    let seenEventCount = 0;
    let consecutiveErrors = 0;

    const settle = async () => {
      if (view) {
        stopElapsed();
        try {
          await completeRun(runId);
        } catch (err) {
          console.warn('[debate] completeRun error:', err);
        }
      }
      resolve();
    };

    const handle = (name: string, payload: DebPayload): void => {
      if (!view) return;

      if (name === 'agent_started') {
        if (payload.agent) {
          markAgent(payload.agent, 'active');
          appendFeedMessage('System', `▶ ${payload.agent} began analysis (${payload.model || 'Gemini'})...`);
        }
        if (view.state === 'running') {
          setStateLabel('running');
        }
      } else if (name === 'agent_finished') {
        if (payload.agent) {
          markAgent(payload.agent, 'done');
          const durStr = payload.duration ? ` in ${payload.duration}s` : '';
          appendFeedMessage('System', `✓ ${payload.agent} finished analysis${durStr}.`);
        }
      } else if (name === 'agent_turn') {
        if (payload.agent && payload.text) {
          appendFeedMessage(payload.agent, payload.text);
        }
      } else if (name === 'clarify_question' || name === 'clarify' || name === 'run_paused') {
        const q = payload.question || payload.text || '';
        showClarifyBox(q);
        appendFeedMessage('Orchestrator', `⏸ **Clarification & Decision Gate**\n\n${q}`);
      } else if (name === 'verdict') {
        renderVerdictGate(payload);
        appendFeedMessage('Judge', payload.verdict_text || payload.text || `Verdict: ${payload.verdict}`);
      } else if (name === 'run_finished') {
        if (view) {
          view.state = 'done';
          stopElapsed();
          setStateLabel('done');
          document.getElementById('btn-stop')?.classList.add('hidden');
        }
        void settle();
      } else if (name === 'run_stopped') {
        if (view) {
          view.state = 'stopped';
          stopElapsed();
          setStateLabel('stopped');
          appendFeedMessage('System', 'Debate stopped on the server.');
        }
        resolve();
      } else if (name === 'run_failed') {
        fail(payload.reason ?? 'Debate execution failed');
        resolve();
      } else if (name === 'expired') {
        fail('Run expired on the server');
        resolve();
      }
    };

    const poll = async () => {
      while (view && view.state === 'running') {
        try {
          const st = await api.fetchStatus(runId, 4000);
          if (st) {
            consecutiveErrors = 0;
            if (st.events && Array.isArray(st.events)) {
              while (seenEventCount < st.events.length) {
                const ev = st.events[seenEventCount++];
                if (ev && ev.event) {
                  handle(ev.event, (ev.data || {}) as DebPayload);
                }
              }
            }
            if (st.status === 'failed') {
              fail(st.error || 'Debate execution failed on the server');
              resolve();
              return;
            }
            if (st.status === 'stopped') {
              if (view) {
                view.state = 'stopped';
                stopElapsed();
                setStateLabel('stopped');
                appendFeedMessage('System', 'Debate stopped.');
              }
              resolve();
              return;
            }
            if (st.status === 'done' || st.status === 'needs_approval' || st.status === 'needs_verdict') {
              setStateLabel('done');
              void settle();
              return;
            }
          } else {
            consecutiveErrors++;
          }
          const result = await api.fetchResult(runId, 4000);
          if (result?.result && (result.result as Record<string, unknown>).status !== 'failed') {
            setStateLabel('done');
            void settle();
            return;
          }
        } catch {
          consecutiveErrors++;
        }

        if (consecutiveErrors >= 10) {
          fail('Lost connection to the debate server. Please check your network and refresh.');
          resolve();
          return;
        }

        await new Promise((r) => setTimeout(r, 1000));
      }
    };

    void poll();
  });
}

function showClarifyBox(question: string): void {
  const box = document.getElementById('clarify-box');
  const qEl = document.getElementById('clarify-question');
  if (!box || !qEl) return;
  qEl.innerHTML = renderMarkdown(question);
  box.classList.remove('hidden');
  box.scrollIntoView({ behavior: 'smooth' });
  setStateLabel('paused');

  const sendBtn = document.getElementById('clarify-send') as HTMLButtonElement | null;
  const input = document.getElementById('clarify-answer') as HTMLInputElement | null;

  // Render quick reply pills
  let quickWrap = document.getElementById('clarify-quick-actions');
  if (!quickWrap) {
    quickWrap = dom('div', 'flex gap-2 mt-2 flex-wrap');
    quickWrap.id = 'clarify-quick-actions';
    box.appendChild(quickWrap);
  }
  quickWrap.innerHTML = '';

  const submitAnswer = async (ans: string) => {
    if (!ans || !view) return;
    if (sendBtn) sendBtn.disabled = true;
    setStateLabel('resuming');
    try {
      await api.clarifyDebate(view.runId, ans, currentApiKey);
      appendFeedMessage('Human', `Answer: ${ans}`);
      if (input) input.value = '';
      box.classList.add('hidden');
    } catch (err) {
      alert('Could not submit answer: ' + String(err));
    } finally {
      if (sendBtn) sendBtn.disabled = false;
    }
  };

  const actions = [
    { label: '✅ [Approve] Proceed to PRD', val: '[Approve]' },
    { label: '✏️ [Changes] Suggest Pivots', val: '[Changes]: ' },
    { label: '🛑 [Reject] Abandon Idea', val: '[Reject]' },
  ];

  for (const act of actions) {
    const b = dom('button', 'px-3 py-1.5 rounded-lg bg-amber-900/60 hover:bg-amber-800 text-amber-200 text-xs font-bold border border-amber-600/60 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-400', act.label);
    b.setAttribute('type', 'button');
    b.onclick = () => {
      if (act.val.endsWith(': ')) {
        if (input) {
          input.value = act.val;
          input.focus();
        }
      } else {
        void submitAnswer(act.val);
      }
    };
    quickWrap.appendChild(b);
  }

  if (sendBtn && input) {
    sendBtn.onclick = () => {
      const ans = input.value.trim();
      if (ans) void submitAnswer(ans);
    };
    input.onkeydown = (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const ans = input.value.trim();
        if (ans) void submitAnswer(ans);
      }
    };
    setTimeout(() => input.focus(), 50);
  }
}

async function completeRun(runId: string): Promise<void> {
  if (!view) return;

  let resultData: api.RunResult | null = null;
  try {
    resultData = await api.fetchResult(runId);
  } catch (err) {
    console.warn('[debate] fetchResult notice:', err);
  }

  const resObj = (resultData?.result || {}) as Record<string, unknown>;
  const prdText = (resObj.prd as string) || '';
  const verdictRaw = resObj.verdict as Record<string, unknown> | string | undefined;
  let verdictStr = 'PARK';
  if (typeof verdictRaw === 'string') {
    verdictStr = verdictRaw;
  } else if (verdictRaw && typeof verdictRaw === 'object') {
    verdictStr = (verdictRaw.verdict as string) || (verdictRaw.status as string) || 'PARK';
  }

  const rawScores = typeof verdictRaw === 'object' && verdictRaw !== null ? (verdictRaw as { scores?: any })?.scores : undefined;
  let scores: { novelty?: number; feasibility?: number; market_fit?: number } | undefined;
  if (rawScores) {
    const getVal = (v: any) => (typeof v === 'object' && v !== null && 'score' in v ? v.score : typeof v === 'number' ? v : undefined);
    scores = {
      novelty: getVal(rawScores.novelty),
      feasibility: getVal(rawScores.feasibility),
      market_fit: getVal(rawScores.market_fit),
    };
  }

  // If no result was generated or the status is failed, fail loudly instead of falsely celebrating
  if (!resultData?.result || resObj.status === 'failed' || (!prdText && !resObj.verdict && !resObj.research_brief)) {
    const errorMsg = (resObj.error as string) || (resultData as any)?.error || 'Debate finished without generating a verdict.';
    fail(errorMsg);

    const failedRunRecord: IdeaRun = {
      run_id: runId,
      run_number: (view.idea.runs || []).length + 1,
      status: 'failed',
      started_at: view.startedAt,
      finished_at: Date.now(),
      events: view.events,
    };
    const updatedIdea = await store.saveRunResult(view.idea.id, failedRunRecord);
    if (updatedIdea && view.onFinish) {
      view.onFinish(updatedIdea);
    }
    return;
  }

  view.state = 'done';
  stopElapsed();
  setStateLabel('done');
  ensureErrorHidden();
  document.getElementById('btn-stop')?.classList.add('hidden');

  if (prdText) {
    renderPrdCard(prdText, resObj.security_audit as Record<string, unknown>);
  }

  const donePanel = document.getElementById('debate-done');
  if (donePanel) {
    donePanel.classList.remove('hidden');
    const t = document.getElementById('debate-done-title');
    if (t) t.textContent = '🎉 Debate complete — PRD and evaluation ready!';
  }

  const runRecord: IdeaRun = {
    run_id: runId,
    run_number: (view.idea.runs || []).length + 1,
    status: 'DONE',
    started_at: view.startedAt,
    finished_at: Date.now(),
    verdict: verdictStr,
    verdict_text: (resObj.verdict_text as string) || '',
    scores,
    prd_text: prdText,
    research_brief: (resObj.research_brief as string) || '',
    advocate_argument: (resObj.advocate_argument as string) || '',
    critic_rebuttal: (resObj.critic_rebuttal as string) || '',
    creative_angles: (resObj.creative_angles as string) || '',
    security_audit: resObj.security_audit as Record<string, unknown>,
    events: view.events,
    turns_used: (resObj.turns_used as number) || 0,
  };

  const updatedIdea = await store.saveRunResult(view.idea.id, runRecord);

  try {
    await api.ackResult(runId);
  } catch {
    // ignore
  }

  if (updatedIdea && view.onFinish) {
    view.onFinish(updatedIdea);
  }
}