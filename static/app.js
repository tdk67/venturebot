let authChecked = false;

// Prototype phase: no login. Show the app immediately as a local user.
// Re-enable the login gate in the multi-user phase (see PROTOTYPE_NO_LOGIN.md).
function showApp(user) {
  document.getElementById('app').classList.remove('hidden');
  document.getElementById('user-badge').textContent = (user && user.email) || 'local';
  connectSSE();
  fetchState();
  fetchIdeas();
  fetchFacets();
  fetchCheckpoints();
}

// ── XSS-safe rendering: ALWAYS use textContent, never innerHTML ────────
function el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;  // safe
  return e;
}

// ── Debate feed ────────────────────────────────────────────────────────
const feed = () => document.getElementById('debate-feed');

function addMessage(agent, text) {
  const div = el('div', 'msg p-3 rounded-lg bg-slate-800 text-slate-100 text-xs font-mono');
  const head = el('span', 'text-yellow-400', '[' + agent + '] ');
  div.appendChild(head);
  div.appendChild(document.createTextNode(text));  // safe
  feed().appendChild(div);
  feed().scrollTop = feed().scrollHeight;
}

// ── State ──────────────────────────────────────────────────────────────
async function fetchState() {
  const res = await fetch('/api/state');
  if (res.status === 401) { location.reload(); return; }
  const data = await res.json();
  // Durable pause: if a debate is paused on a clarifying question (possibly
  // from days ago / across server restarts), re-show the answer box.
  if (data.pending_clarification) {
    const box = document.getElementById('clarify-box');
    if (box.classList.contains('hidden')) {
      showClarifyBox(data.pending_clarification.question,
                     data.pending_clarification.run_id);
    }
  } else {
    hideClarifyBox();
  }
  const badge = document.getElementById('status-badge');
  badge.textContent = (data.status || 'idle').toUpperCase();
  badge.className = 'px-3 py-1 text-xs font-bold rounded-full border ' +
    ({idle:'bg-slate-600', running:'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
      approved:'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
      waiting_user:'bg-amber-500/10 text-amber-400 border-amber-500/20',
      failed:'bg-red-500/10 text-red-400 border-red-400/20',
      stopped:'bg-amber-500/10 text-amber-400 border-amber-400/20'}[data.status] || 'bg-slate-600');
  if (data.budget) {
    document.getElementById('budget-badge').textContent =
      `budget: $${data.budget.spent}/${data.budget.limit}`;
  }
  if (data.usage) {
    document.getElementById('usage-toggle').textContent =
      `usage: ${data.usage.calls} calls · $${data.usage.total_cost.toFixed(4)}`;
  }
}

let _usagePeriod = 'today';
async function fetchUsage(period) {
  _usagePeriod = period || _usagePeriod;
  const res = await fetch('/api/usage?period=' + _usagePeriod);
  if (res.status === 401) return;
  const d = await res.json();
  const body = document.getElementById('usage-body');
  body.innerHTML = '';
  body.appendChild(el('div', 'text-slate-400', `Total: ${d.total_calls} calls · $${d.total_cost.toFixed(4)}`));
  const tbl = document.createElement('table');
  tbl.className = 'w-full mt-2';
  (d.per_model || []).forEach(m => {
    const tr = document.createElement('tr');
    tr.appendChild(el('td', 'py-1 pr-4 text-slate-300', m.model));
    tr.appendChild(el('td', 'py-1 pr-4 text-slate-400', `${m.calls} calls`));
    tr.appendChild(el('td', 'py-1 text-slate-400', `$${m.cost.toFixed(4)}`));
    tbl.appendChild(tr);
  });
  body.appendChild(tbl);
}
function toggleUsage() {
  const p = document.getElementById('usage-panel');
  p.classList.toggle('hidden');
  if (!p.classList.contains('hidden')) fetchUsage(_usagePeriod);
}

// ── Control ────────────────────────────────────────────────────────────
async function doRun() {
  const idea = document.getElementById('idea-input').value.trim();
  if (!idea) return;
  const btn = document.getElementById('btn-run');
  const urlStr = document.getElementById('url-input').value.trim();
  const urls = urlStr ? urlStr.split(',').map(s => s.trim()).filter(Boolean) : [];

  // Duplicate check before submitting (UI_UX_NOTES #4).
  try {
    const dupRes = await fetch('/api/ideas/duplicate-check', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ title: idea }),
    });
    if (dupRes.ok) {
      const dup = await dupRes.json();
      if (dup.duplicates && dup.duplicates.length) {
        const names = dup.duplicates.map(d => `"${d.title}" (${d.status})`).join(', ');
        if (!confirm(`You may have submitted a similar idea before: ${names}.\n\nSubmit anyway?`)) return;
      }
    }
  } catch (e) { /* duplicate check is best-effort */ }

  // Immediate feedback + debounce (fixes UI_UX_NOTES #1).
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = '⏳ Starting…';
  let res;
  try {
    res = await fetch('/api/run-phase1', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ idea, urls }),
    });
  } finally {
    // Re-enable after a short lockout; the SSE 'run_started' event drives the
    // running indicator from here on.
    setTimeout(() => { btn.disabled = false; btn.textContent = original; }, 600);
  }
  addMessage('System', 'Debate started: ' + idea + (urls.length ? ` (${urls.length} URL(s) ingested)` : ''));
}

async function doSteering() {
  const text = document.getElementById('steering-input').value.trim();
  if (!text) return;
  const res = await fetch('/api/steering', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ text }),
  });
  const data = await res.json();
  document.getElementById('steering-input').value = '';
  document.getElementById('steering-status').textContent = 'Queued — will be ingested at next checkpoint.';
  addMessage('Human', '🛞 Steering: ' + text);
}

async function doStop() {
  await fetch('/api/stop', {method: 'POST'});
  addMessage('System', '⏹ Stop requested.');
}

async function verdictAction(action) {
  if (action === 'rebut') {
    // Show the rebut box instead of immediately deciding (UI_UX_NOTES #3).
    addMessage('Human', '↻ Rebut — explain what the debate missed');
    document.getElementById('rebut-box').classList.remove('hidden');
    return;
  }
  addMessage('Human', action === 'proceed' ? 'PROCEED ANYWAY' : 'ABORT');
  document.getElementById('verdict-card').classList.add('hidden');
  const runId = await getCurrentRunId();
  if (action === 'proceed' && runId) {
    await fetch('/api/resume', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ run_id: runId, decision: 'proceed' }),
    });
    addMessage('System', 'Proceeding to PRD Writer...');
  } else {
    await fetch('/api/resume', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ run_id: runId, decision: 'abort' }),
    }).catch(()=>{});
  }
}

async function submitRebut() {
  const text = document.getElementById('rebut-text').value.trim();
  if (!text) return;
  addMessage('Human', '🛞 Rebuttal: ' + text);
  document.getElementById('verdict-card').classList.add('hidden');
  document.getElementById('rebut-box').classList.add('hidden');
  document.getElementById('rebut-text').value = '';
  const runId = await getCurrentRunId();
  if (runId) {
    await fetch('/api/resume', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ run_id: runId, decision: 'rebut', steering: text }),
    }).catch(()=>{});
    addMessage('System', 'Re-evaluating with your rebuttal…');
  }
}

async function prdAction(action) {
  addMessage('Human', action === 'approve' ? 'APPROVED PRD' : 'REJECTED PRD');
  document.getElementById('prd-card').classList.add('hidden');
  const runId = await getCurrentRunId();
  if (runId) {
    await fetch('/api/resume', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ run_id: runId, decision: action }),
    }).catch(()=>{});
  }
}

async function getCurrentRunId() {
  const res = await fetch('/api/state');
  const data = await res.json();
  return data.run_id;
}

// ── SSE ────────────────────────────────────────────────────────────────
// Participants of the debate, in pipeline order. The Orchestrator is a
// participant too — it drives the loop between phases and decides when to stop.
const _PARTICIPANTS = [
  { key: 'orchestrator', label: '🎛 Orchestrator' },
  { key: 'research',     label: '🔍 Researcher' },
  { key: 'advocate',     label: '⚖️ Advocate' },
  { key: 'critic',       label: '🛡️ Critic' },
  { key: 'creative',     label: '💡 Creative' },
  { key: 'judge',        label: '🧑‍⚖️ Judge' },
  { key: 'prd_writer',   label: '📝 PRD Writer' },
  { key: 'auditor',      label: '✅ Auditor' },
];

const _chipState = {};   // key → { chip, status, timeEl, timer, startedAt }
let _econTimer = null;
let _costBaseline = null; // budget.spent when the debate started
let _activeChip = null;

function _fmtSecs(s) {
  return Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0');
}

function buildParticipants() {
  const box = document.getElementById('participant-chips');
  if (box.childElementCount) return;  // build once
  _PARTICIPANTS.forEach(p => {
    const chip = el('div', 'flex items-center gap-2 px-3 py-1.5 rounded-full border border-slate-700 bg-slate-900 text-xs text-slate-400');
    chip.appendChild(el('span', '', p.label));
    const status = el('span', 'text-slate-600', '·');
    const time = el('span', 'font-mono text-slate-600', '');
    chip.appendChild(status);
    chip.appendChild(time);
    box.appendChild(chip);
    _chipState[p.key] = { chip, status, timeEl: time, timer: null };
  });
}

function chipSet(key, mode) {
  // mode: idle | thinking | done | error
  const st = _chipState[key];
  if (!st) return;
  if (st.timer) { clearInterval(st.timer); st.timer = null; }
  const base = 'flex items-center gap-2 px-3 py-1.5 rounded-full border text-xs ';
  if (mode === 'thinking') {
    st.chip.className = base + 'border-blue-500 bg-blue-500/10 text-blue-300';
    st.status.textContent = '🧠 thinking…';
    st.status.className = 'text-blue-400';
    st.timeEl.className = 'font-mono text-blue-300';
    st.startedAt = Date.now();
    st.timeEl.textContent = '0:00';
    st.timer = setInterval(() => {
      st.timeEl.textContent = _fmtSecs((Date.now() - st.startedAt) / 1000);
    }, 1000);
  } else if (mode === 'done') {
    st.chip.className = base + 'border-emerald-500/40 bg-emerald-500/5 text-emerald-300';
    st.status.textContent = '✓ done';
    st.status.className = 'text-emerald-400';
    st.timeEl.className = 'font-mono text-emerald-400/80';
    if (!st.timeEl.textContent) st.timeEl.textContent = '0:00';
  } else if (mode === 'error') {
    st.chip.className = base + 'border-red-500/50 bg-red-500/10 text-red-300';
    st.status.textContent = '✗ failed';
    st.status.className = 'text-red-400';
  } else {
    st.chip.className = base + 'border-slate-700 bg-slate-900 text-slate-400';
    st.status.textContent = '·';
    st.status.className = 'text-slate-600';
    st.timeEl.textContent = '';
    st.timeEl.className = 'font-mono text-slate-600';
  }
}

function chipActivate(key) {
  // Complete whichever chip is currently thinking, then start this one.
  if (_activeChip && _activeChip !== key) chipSet(_activeChip, 'done');
  _activeChip = key;
  if (key) chipSet(key, 'thinking');
}

function participantsReset() {
  buildParticipants();
  document.getElementById('participants').classList.remove('hidden');
  Object.keys(_chipState).forEach(k => chipSet(k, 'idle'));
  _activeChip = null;
}

function participantsStopAll(failedKey) {
  Object.keys(_chipState).forEach(k => {
    const st = _chipState[k];
    if (st.timer) { clearInterval(st.timer); st.timer = null; }
    if (st.timeEl.textContent) chipSet(k, k === failedKey ? 'error' : 'done');
  });
}

function econShow() {
  document.getElementById('debate-econ').classList.remove('hidden');
  document.getElementById('econ-cost').textContent = '💰 $0.0000';
  document.getElementById('econ-turns').textContent = '';
  document.getElementById('econ-elapsed').textContent = '⏱ 0:00';
  if (_econTimer) clearInterval(_econTimer);
  const t0 = Date.now();
  _econTimer = setInterval(() => {
    document.getElementById('econ-elapsed').textContent = '⏱ ' + _fmtSecs((Date.now() - t0) / 1000);
  }, 1000);
  // Live debate cost = delta of today's spend since the run started.
  fetch('/api/state').then(r => r.json()).then(d => {
    _costBaseline = (d.budget && typeof d.budget.spent === 'number') ? d.budget.spent : 0;
  }).catch(() => { _costBaseline = 0; });
}

function econUpdateCost() {
  if (_costBaseline === null) return;
  fetch('/api/state').then(r => r.json()).then(d => {
    const spent = (d.budget && typeof d.budget.spent === 'number') ? d.budget.spent : 0;
    document.getElementById('econ-cost').textContent =
      '💰 $' + Math.max(0, spent - _costBaseline).toFixed(4);
  }).catch(() => {});
}
setInterval(econUpdateCost, 5000);

function econFinish(final) {
  if (_econTimer) { clearInterval(_econTimer); _econTimer = null; }
  econUpdateCost();
  if (final && typeof final.cost === 'number') {
    document.getElementById('econ-cost').textContent = '💰 $' + final.cost.toFixed(4);
  }
  if (final && typeof final.elapsed_seconds === 'number') {
    document.getElementById('econ-elapsed').textContent = '⏱ done in ' + _fmtSecs(final.elapsed_seconds);
  }
  if (final && typeof final.turns_used === 'number') {
    document.getElementById('econ-turns').textContent = final.turns_used + ' orchestrator turns';
  }
}

// ── Agent output rendering ─────────────────────────────────────────────
// Structured agent output (JSON schemas like ResearchBrief) is rendered as
// readable sections instead of a raw JSON dump.
function looksLikeJson(text) {
  const t = (text || '').trim();
  return t.startsWith('{') || t.startsWith('[');
}

// Convert structured JSON (e.g. ResearchBrief) to readable markdown for
// exports. Top-level keys become sections; arrays of objects become bullet
// lists with bold labels.
function jsonToMarkdown(obj, level = 2) {
  const human = k => String(k).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  const heading = n => '#'.repeat(Math.min(Math.max(n, 2), 6));
  const clean = v => v !== null && v !== undefined && v !== '';
  if (!clean(obj)) return '';
  if (typeof obj !== 'object') return String(obj);
  if (Array.isArray(obj)) {
    if (!obj.length) return '';
    return obj.map(item => {
      if (item && typeof item === 'object') {
        const parts = Object.entries(item).filter(([, v]) => clean(v))
          .map(([k, v]) => typeof v === 'object' ? null : `**${human(k)}:** ${v}`)
          .filter(Boolean);
        const nested = Object.entries(item).filter(([, v]) => v && typeof v === 'object' && clean(v))
          .map(([, v]) => '\n  - ' + String(jsonToMarkdown(v, level + 1)).split('\n').join('\n    '))
          .join('');
        return `- ${parts.join(' — ')}${nested}`;
      }
      return `- ${item}`;
    }).filter(l => l !== '- ').join('\n');
  }
  return Object.entries(obj).map(([k, v]) => {
    if (!clean(v)) return '';
    if (typeof v === 'object') return `${heading(level)} ${human(k)}\n\n${jsonToMarkdown(v, level + 1)}`;
    return `- **${human(k)}:** ${v}`;
  }).filter(Boolean).join('\n\n');
}

// If text is JSON, return it as readable markdown; otherwise return as-is.
function formatDocText(text) {
  const t = (text || '').trim();
  if (!looksLikeJson(t)) return t || text || '';
  try { return jsonToMarkdown(JSON.parse(t)) || t; } catch (_) { return t; }
}

function appendStructuredJson(container, obj, depth) {
  depth = depth || 0;
  if (Array.isArray(obj)) {
    obj.forEach(item => {
      if (item && typeof item === 'object') {
        const li = el('div', 'pl-3 border-l border-slate-700 my-1');
        container.appendChild(li);
        appendStructuredJson(li, item, depth + 1);
      } else {
        container.appendChild(el('div', 'pl-3 text-slate-200 my-0.5', '• ' + String(item)));
      }
    });
    return;
  }
  Object.entries(obj).forEach(([k, v]) => {
    if (v === null || v === undefined || v === '') return;
    const row = el('div', depth === 0 ? 'my-2' : 'my-1');
    row.appendChild(el('span', 'font-bold text-blue-300', k.replace(/_/g, ' ') + ': '));
    if (typeof v === 'object') {
      const sub = el('div', depth === 0 ? '' : '');
      row.appendChild(sub);
      appendStructuredJson(sub, v, depth + 1);
    } else {
      const val = el('span', 'text-slate-100 whitespace-pre-wrap', String(v));
      row.appendChild(val);
    }
    container.appendChild(row);
  });
}

function renderAgentOutput(container, text) {
  // JSON → structured view; markdown → sanitized HTML (XSS-safe via DOMPurify);
  // fallback → plain text. innerHTML is only ever set with sanitized output.
  if (looksLikeJson(text)) {
    try {
      const obj = JSON.parse(text.trim());
      appendStructuredJson(container, obj, 0);
      return;
    } catch (_) { /* fall through */ }
  }
  if (window.marked && window.DOMPurify) {
    try {
      container.classList.add('md-body');
      container.innerHTML = DOMPurify.sanitize(marked.parse(text), {
        ALLOWED_ATTR: ['href', 'title', 'alt'],
        FORBID_TAGS: ['style', 'form', 'input', 'iframe'],
      });
      return;
    } catch (_) { /* fall through to plain text */ }
  }
  container.classList.add('whitespace-pre-wrap');
  container.appendChild(document.createTextNode(text));
  return;
}

function addAgentMessage(agent, text) {
  const div = el('div', 'msg p-3 rounded-lg bg-slate-800 text-slate-100 text-xs font-mono');
  div.appendChild(el('span', 'text-yellow-400', '[' + agent + '] '));
  const body = el('div', '');
  renderAgentOutput(body, text);
  div.appendChild(body);
  feed().appendChild(div);
  feed().scrollTop = feed().scrollHeight;
}

// ── Clarify (orchestrator → human question) ──────────────────────────
function showClarifyBox(question, runId) {
  const box = document.getElementById('clarify-box');
  document.getElementById('clarify-question').textContent = question;
  box.dataset.runId = runId || '';
  box.classList.remove('hidden');
  feed().scrollTop = feed().scrollHeight;
}
function hideClarifyBox() {
  document.getElementById('clarify-box').classList.add('hidden');
}
async function sendClarifyAnswer() {
  const box = document.getElementById('clarify-box');
  const input = document.getElementById('clarify-answer');
  const answer = input.value.trim();
  if (!answer) return;
  const btn = document.getElementById('clarify-send');
  btn.disabled = true;
  try {
    const res = await fetch('/api/clarify/answer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_id: box.dataset.runId, answer }),
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    addMessage('Human', answer);
    input.value = '';
    hideClarifyBox();
  } catch (err) {
    addMessage('System', 'ERROR: could not send answer — ' + err.message + ' (the orchestrator waits 10 minutes, try again)');
  } finally {
    btn.disabled = false;
  }
}
document.addEventListener('click', (e) => {
  if (e.target && e.target.id === 'clarify-send') sendClarifyAnswer();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target && e.target.id === 'clarify-answer' && !e.isComposing) sendClarifyAnswer();
});

function connectSSE() {
  const es = new EventSource('/api/events');
  es.addEventListener('agent_message', (e) => {
    const d = JSON.parse(e.data);
    addAgentMessage(d.agent, d.text);
  });
  es.addEventListener('clarify', (e) => {
    const d = JSON.parse(e.data);
    addMessage('Orchestrator', '❓ ' + d.question);
    showClarifyBox(d.question, d.run_id);
  });
  es.addEventListener('clarify_answered', () => hideClarifyBox());
  es.addEventListener('run_started', (e) => {
    participantsReset();
    econShow();
    chipActivate('orchestrator');
    addMessage('System', '🎛 Orchestrator took over — planning the debate…');
  });
  es.addEventListener('phase_started', (e) => {
    const d = JSON.parse(e.data);
    participantsReset();
    chipActivate(d.phase);
  });
  es.addEventListener('phase_done', (e) => {
    const d = JSON.parse(e.data);
    if (_activeChip === d.phase) { chipSet(d.phase, 'done'); _activeChip = null; }
    chipActivate('orchestrator');  // orchestrator decides what happens next
  });
  es.addEventListener('orchestrator_decision', (e) => {
    const d = JSON.parse(e.data);
    if (_activeChip && _activeChip !== 'orchestrator') chipSet(_activeChip, 'done');
    _activeChip = 'orchestrator';
    chipSet('orchestrator', 'thinking');
    addMessage('Orchestrator',
      d.decision === 'stop'
        ? `🛑 Decision: STOP the loop (${d.reason}) — after ${d.turns_used}/${d.max_turns} turns. Presenting results.`
        : `▶ Decision: CONTINUE (${d.reason}).`);
  });
  es.addEventListener('agent_turn', (e) => {
    const d = JSON.parse(e.data);
    addAgentMessage(d.agent, d.text);
  });
  es.addEventListener('human_comment', (e) => {
    const d = JSON.parse(e.data);
    addMessage('Human', '💬 ' + d.comment);
  });
  es.addEventListener('run_paused', (e) => {
    const d = JSON.parse(e.data);
    participantsStopAll();
    addMessage('System', '⏸ Debate paused — waiting for your answer below. No time limit; the state is saved, you can come back later (even after closing the browser).');
    fetchState();
  });
  es.addEventListener('run_finished', (e) => {
    const d = JSON.parse(e.data);
    participantsStopAll(d.status === 'failed' ? _activeChip : null);
    econFinish(d);
    if (d.creative_angles) addAgentMessage('Creative', d.creative_angles);
    if (d.verdict) showVerdict(d.verdict);
    if (d.has_prd && d.prd) showPRD(d.prd, d.security_audit);
    if (d.error) addMessage('System', 'ERROR: ' + d.error);
    fetchState();
  });
  es.addEventListener('stopped', () => { participantsStopAll(); econFinish(null); fetchState(); });
  es.onerror = () => { /* EventSource auto-reconnects */ };
}

function showVerdict(v) {
  document.getElementById('verdict-card').classList.remove('hidden');
  const scoresEl = document.getElementById('verdict-scores');
  scoresEl.innerHTML = '';
  const s = v.scores || {};
  ['novelty', 'feasibility', 'market_fit'].forEach(k => {
    const item = s[k] || {};
    const cell = el('div', 'bg-slate-800 p-3 rounded-lg');
    cell.appendChild(el('div', 'text-xs uppercase text-slate-400', k));
    cell.appendChild(el('div', 'text-2xl font-black', item.score !== undefined ? item.score + '/10' : '—'));
    cell.appendChild(el('div', 'text-xs text-slate-400', item.rationale || ''));
    scoresEl.appendChild(cell);
  });
  document.getElementById('verdict-rationale').textContent = v.verdict_rationale || '';
  const risksEl = document.getElementById('verdict-key-risks');
  risksEl.innerHTML = '';
  (v.key_risks || []).forEach(r => {
    risksEl.appendChild(el('div', 'text-xs text-slate-400 mt-1', '⚠ ' + r));
  });
}

function showPRD(prd, audit) {
  document.getElementById('prd-card').classList.remove('hidden');
  document.getElementById('prd-text').textContent = prd;  // safe
  const badge = document.getElementById('audit-badge');
  const findingsEl = document.getElementById('audit-findings');
  badge.innerHTML = ''; findingsEl.innerHTML = '';
  if (audit) {
    const ok = audit.ok;
    const b = el('span',
      ok ? 'px-3 py-1 text-xs font-bold rounded-full border bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
         : 'px-3 py-1 text-xs font-bold rounded-full border bg-red-500/10 text-red-400 border-red-400/20',
      ok ? '✓ Security audit PASS' : '⚠ Security audit FLAG');
    badge.appendChild(b);
    (audit.findings || []).forEach(f => {
      findingsEl.appendChild(el('div', 'text-xs text-red-300 mt-1',
        `[${f.severity || '?'}/${f.category || f.section || '?'}] ${f.detail || f.issue || JSON.stringify(f)}`));
    });
  }
}

// ── Past ideas + checkpoints (idea history) ───────────────────────────
let ideasPage = 1;
let activeFilters = { category: null, year: null, month: null, status: null, search: '' };

async function fetchIdeas() {
  const q = document.getElementById('ideas-search').value.trim();
  activeFilters.search = q;
  const params = new URLSearchParams({ page: String(ideasPage) });
  if (q) params.set('search', q);
  if (activeFilters.status) params.set('status', activeFilters.status);
  if (activeFilters.category) params.set('category', activeFilters.category);
  if (activeFilters.year) params.set('date_year', String(activeFilters.year));
  if (activeFilters.month) params.set('date_month', String(activeFilters.month));
  const res = await fetch('/api/ideas?' + params.toString());
  if (res.status === 401) return;
  const d = await res.json();
  renderIdeas(d);
  renderActiveFilters();
}

function renderActiveFilters() {
  const el = document.getElementById('active-filters');
  const parts = [];
  if (activeFilters.category) parts.push('category: ' + activeFilters.category);
  if (activeFilters.year) parts.push('year: ' + activeFilters.year);
  if (activeFilters.month) parts.push('month: ' + activeFilters.month);
  if (activeFilters.status) parts.push('status: ' + activeFilters.status);
  if (parts.length) {
    el.textContent = 'Filtering by ' + parts.join(', ') + ' — ';
    const clear = document.createElement('button');
    clear.textContent = 'clear';
    clear.className = 'underline text-blue-400';
    clear.onclick = () => {
      activeFilters = { category: null, year: null, month: null, status: null, search: '' };
      document.getElementById('ideas-search').value = '';
      ideasPage = 1;
      fetchIdeas();
      fetchFacets();
    };
    el.appendChild(clear);
    el.classList.remove('hidden');
  } else {
    el.classList.add('hidden');
  }
}

function renderIdeas(d) {
  const list = document.getElementById('ideas-list');
  const empty = document.getElementById('ideas-empty');
  const pager = document.getElementById('ideas-pager');
  list.innerHTML = '';
  empty.classList.toggle('hidden', d.total !== 0);
  (d.items || []).forEach(idea => {
    const card = el('div', 'p-3 rounded-lg bg-slate-800/60 border border-slate-700 hover:border-blue-500 cursor-pointer');
    card.onclick = () => openIdeaModal(idea.id);
    const dot = {ACTIVE:'🟢', PARK:'🟡', PRUNED:'🔴'}[idea.status] || '⚪';
    const row = el('div', 'flex items-center justify-between');
    row.appendChild(el('div', 'text-sm font-bold text-slate-100', `${dot} ${idea.title}`));
    row.appendChild(el('span', 'text-xs text-slate-400', idea.date));
    card.appendChild(row);
    card.appendChild(el('div', 'text-xs text-slate-400 mt-1', idea.description));
    const tags = el('div', 'flex flex-wrap gap-1 mt-2');
    if (idea.run_count > 1) {
      tags.appendChild(el('span', 'px-2 py-0.5 text-[10px] rounded bg-blue-900/60 text-blue-300', `${idea.run_count} debates`));
    }
    (idea.tags || []).forEach(t => tags.appendChild(el('span', 'px-2 py-0.5 text-[10px] rounded bg-slate-700 text-slate-300', t)));
    card.appendChild(tags);
    const actions = el('div', 'flex gap-2 mt-2');
    actions.onclick = (e) => e.stopPropagation();  // don't open the modal from action buttons
    if (idea.has_prd) {
      const viewBtn = el('button', 'px-2 py-1 text-[11px] rounded bg-slate-700 hover:bg-slate-600 text-white', 'View PRD');
      viewBtn.onclick = () => openIdeaModal(idea.id, { showPrd: true });
      actions.appendChild(viewBtn);
    }
    // Export this idea (+ full debate history) as JSON
    const expBtn = el('button', 'px-2 py-1 text-[11px] rounded bg-slate-700 hover:bg-slate-600 text-white', '⬇ JSON');
    expBtn.title = 'Export this idea + full history as JSON';
    expBtn.onclick = () => { window.location.href = `/api/ideas/${idea.id}/export`; };
    actions.appendChild(expBtn);
    if (idea.status !== 'PARK') {
      const parkBtn = el('button', 'px-2 py-1 text-[11px] rounded bg-slate-700 hover:bg-slate-600 text-white', 'Park');
      parkBtn.onclick = async () => { await fetch(`/api/ideas/${idea.id}/archive`, {method:'POST'}); fetchIdeas(); fetchFacets(); };
      actions.appendChild(parkBtn);
    }
    // Delete idea (UI_UX_NOTES #5)
    const delBtn = el('button', 'px-2 py-1 text-[11px] rounded bg-red-700/60 hover:bg-red-600 text-white', 'Delete');
    delBtn.onclick = async () => {
      if (!confirm(`Delete "${idea.title}" and all its research/debate/PRD data? This cannot be undone.`)) return;
      await fetch(`/api/ideas/${idea.id}`, {method:'DELETE'});
      fetchIdeas(); fetchFacets();
    };
    actions.appendChild(delBtn);
    card.appendChild(actions);
    list.appendChild(card);
  });
  // pagination
  pager.innerHTML = '';
  if (d.total_pages > 1) {
    const prev = el('button', 'px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 text-white', '‹ Prev');
    prev.onclick = () => { ideasPage = Math.max(1, d.page - 1); fetchIdeas(); };
    const next = el('button', 'px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 text-white', 'Next ›');
    next.onclick = () => { ideasPage = Math.min(d.total_pages, d.page + 1); fetchIdeas(); };
    pager.appendChild(prev);
    pager.appendChild(el('span', '', `Page ${d.page} of ${d.total_pages}`));
    pager.appendChild(next);
  }
}

async function fetchFacets() {
  const res = await fetch('/api/ideas/facets');
  if (res.status === 401) return;
  const d = await res.json();

  // Statuses
  const statusEl = document.getElementById('facet-statuses');
  statusEl.innerHTML = '';
  (d.statuses || []).forEach(s => {
    const row = el('button', 'flex justify-between w-full hover:text-white text-slate-400', '');
    row.appendChild(el('span', '', (s.status === 'ACTIVE' ? '🟢 ' : s.status === 'PARK' ? '🟡 ' : '🔴 ') + s.status));
    row.appendChild(el('span', 'text-slate-600', String(s.count)));
    row.onclick = () => {
      activeFilters.status = activeFilters.status === s.status ? null : s.status;
      ideasPage = 1;
      fetchIdeas();
      fetchFacets();
    };
    statusEl.appendChild(row);
  });

  // Tags / categories
  const tagEl = document.getElementById('facet-tags');
  tagEl.innerHTML = '';
  (d.tags || []).forEach(t => {
    const row = el('button', 'flex justify-between w-full hover:text-white text-slate-400', '');
    row.appendChild(el('span', '', t.tag));
    row.appendChild(el('span', 'text-slate-600', String(t.count)));
    row.onclick = () => {
      activeFilters.category = activeFilters.category === t.tag ? null : t.tag;
      ideasPage = 1;
      fetchIdeas();
      fetchFacets();
    };
    tagEl.appendChild(row);
  });

  // Date tree: year → month
  const dateEl = document.getElementById('facet-dates');
  dateEl.innerHTML = '';
  (d.years || []).forEach(y => {
    const yearBtn = el('button', 'flex justify-between w-full font-bold text-slate-300 hover:text-white', '');
    yearBtn.appendChild(el('span', '', String(y.year)));
    yearBtn.appendChild(el('span', 'text-slate-600', String(y.months.reduce((a, m) => a + m.count, 0))));
    yearBtn.onclick = () => {
      activeFilters.year = activeFilters.year === y.year ? null : y.year;
      activeFilters.month = null;
      ideasPage = 1;
      fetchIdeas();
      fetchFacets();
    };
    dateEl.appendChild(yearBtn);
    y.months.forEach(m => {
      const mname = new Date(y.year, m.month - 1, 1).toLocaleString('en', {month: 'short'});
      const mbtn = el('button', 'flex justify-between w-full pl-3 text-slate-400 hover:text-white', '');
      mbtn.appendChild(el('span', '', mname));
      mbtn.appendChild(el('span', 'text-slate-600', String(m.count)));
      mbtn.onclick = () => {
        activeFilters.year = y.year;
        activeFilters.month = activeFilters.month === m.month ? null : m.month;
        ideasPage = 1;
        fetchIdeas();
        fetchFacets();
      };
      dateEl.appendChild(mbtn);
    });
  });
}

async function exportIdeas() {
  const params = new URLSearchParams();
  if (activeFilters.search) params.set('search', activeFilters.search);
  if (activeFilters.status) params.set('status', activeFilters.status);
  if (activeFilters.category) params.set('category', activeFilters.category);
  if (activeFilters.year) params.set('date_year', String(activeFilters.year));
  if (activeFilters.month) params.set('date_month', String(activeFilters.month));
  const url = '/api/ideas/csv' + (params.toString() ? '?' + params.toString() : '');
  window.location.href = url;
}

function exportAllIdeas() {
  window.location.href = '/api/ideas/export';
}

async function importIdeas(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  let text;
  try {
    text = await file.text();
  } catch (e) { alert('Could not read file: ' + e.message); return; }
  let payload;
  try {
    payload = JSON.parse(text);  // validate before POSTing
  } catch (e) { alert('Not a valid JSON file.'); return; }
  if (!confirm(`Import ideas from "${file.name}"? Imported ideas are added alongside existing ones.`)) {
    input.value = '';
    return;
  }
  const res = await fetch('/api/ideas/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert('Import failed: ' + (err.detail || res.status));
    input.value = '';
    return;
  }
  const d = await res.json();
  addMessage('System', `📥 Imported ${d.count} idea(s) — full debate history preserved.`);
  fetchIdeas();
  fetchFacets();
  input.value = '';
}

// ── Idea detail modal: past debates + replay + resume (second brain) ─
let modalIdeaId = null;
let modalIdea = null;   // full idea payload currently shown

async function openIdeaModal(ideaId, opts) {
  const res = await fetch(`/api/ideas/${ideaId}`);
  if (!res.ok) { alert('Could not load idea.'); return; }
  const idea = await res.json();
  modalIdeaId = ideaId;
  modalIdea = idea;
  document.getElementById('idea-resume-comment').value = '';

  renderIdeaHeader(idea);

  renderRunList(idea.runs || []);
  document.getElementById('idea-modal-replay').classList.add('hidden');
  renderModalFooter(idea);

  const modal = document.getElementById('idea-modal');
  modal.classList.remove('hidden');
  window.scrollTo(0, 0);
  if (opts && opts.showPrd && idea.prd_text) {
    // Open the latest run's PRD directly ("View PRD" entry point)
    const runs = idea.runs || [];
    const withPrd = [...runs].reverse().find(r => r.id);  // fall back to latest run
    if (runs.length) showRun(ideaId, withPrd.id, { showPrd: true });
  }
}

function renderIdeaHeader(idea, editMode) {
  // Title row (+ inline edit toggle)
  const wrap = document.getElementById('idea-modal-title-wrap');
  wrap.textContent = '';
  const titleRow = el('div', 'flex items-start justify-between gap-2');
  const dot = {ACTIVE:'🟢', PARK:'🟡', PRUNED:'🔴'}[idea.status] || '⚪';
  if (editMode) {
    const inp = el('input', 'flex-1 bg-slate-900 border border-blue-500 rounded-lg px-3 py-1.5 text-lg font-bold text-slate-100 focus:outline-none');
    inp.value = idea.title;
    inp.id = 'idea-edit-title';
    titleRow.appendChild(inp);
  } else {
    titleRow.appendChild(el('h2', 'text-xl font-bold text-slate-100 break-words', `${dot} ${idea.title}`));
    const editBtn = el('button', 'shrink-0 px-2 py-1 text-[11px] rounded bg-slate-800 hover:bg-slate-700 text-slate-300', '✏️ Edit');
    editBtn.title = 'Edit title & pitch';
    editBtn.onclick = () => renderIdeaHeader(modalIdea, true);
    titleRow.appendChild(editBtn);
  }
  wrap.appendChild(titleRow);

  // Scores / verdict meta line
  const meta = document.getElementById('idea-modal-meta');
  meta.textContent = '';
  if (!editMode) {
    const sc = idea.scores || {};
    const parts = [];
    if (idea.verdict) parts.push('verdict: ' + idea.verdict);
    ['novelty','feasibility','market_fit'].forEach(k => {
      const v = sc[k] && (sc[k].score !== undefined ? sc[k].score : sc[k]);
      if (v !== undefined) parts.push(k + ': ' + v + '/10');
    });
    if (parts.length) meta.appendChild(el('span', '', parts.join(' · ')));
    meta.appendChild(el('span', 'text-slate-600', `  ·  created ${idea.date}`));
  }

  // Full pitch (view or edit)
  const pitchBox = document.getElementById('idea-modal-pitch');
  pitchBox.textContent = '';
  const label = el('div', 'text-[10px] uppercase tracking-wider text-slate-500 font-bold mb-1', 'Full pitch');
  pitchBox.appendChild(label);
  if (editMode) {
    const ta = el('textarea', 'w-full bg-slate-900 border border-blue-500 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none');
    ta.rows = Math.min(10, Math.max(3, Math.ceil((idea.pitch || '').length / 90)));
    ta.value = idea.pitch || '';
    ta.placeholder = 'The complete idea text — this is what a resumed debate receives as input.';
    ta.id = 'idea-edit-pitch';
    pitchBox.appendChild(ta);
    const btns = el('div', 'flex gap-2 mt-2');
    const save = el('button', 'px-3 py-1 text-xs font-bold rounded bg-blue-600 hover:bg-blue-500 text-white', 'Save');
    const cancel = el('button', 'px-3 py-1 text-xs rounded bg-slate-700 hover:bg-slate-600 text-white', 'Cancel');
    save.onclick = async () => {
      const title = document.getElementById('idea-edit-title').value.trim();
      const pitch = document.getElementById('idea-edit-pitch').value.trim();
      if (!title) { alert('Title cannot be empty.'); return; }
      const r = await fetch(`/api/ideas/${modalIdeaId}/edit`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, pitch }),
      });
      if (!r.ok) { const e = await r.json().catch(() => ({})); alert('Save failed: ' + (e.detail || r.status)); return; }
      modalIdea.title = title;
      modalIdea.pitch = pitch || null;
      renderIdeaHeader(modalIdea, false);
      renderModalFooter(modalIdea);
      addMessage('System', '✏️ Idea updated.');
      fetchIdeas();
    };
    cancel.onclick = () => renderIdeaHeader(modalIdea, false);
    btns.appendChild(save);
    btns.appendChild(cancel);
    pitchBox.appendChild(btns);
  } else {
    const text = idea.pitch;
    if (text) {
      const pre = el('pre', 'text-xs text-slate-300 whitespace-pre-wrap bg-slate-900/70 border border-slate-800 p-3 rounded-lg max-h-48 overflow-y-auto', text);
      pitchBox.appendChild(pre);
    } else {
      pitchBox.appendChild(el('p', 'text-xs text-slate-500 italic', 'No full pitch stored (older idea) — click ✏️ Edit to add one; a resumed debate will use it as input.'));
    }
  }
}

function closeIdeaModal() {
  document.getElementById('idea-modal').classList.add('hidden');
  modalIdeaId = null;
}

function renderRunList(runs) {
  const box = document.getElementById('idea-modal-runs');
  box.textContent = '';
  if (!runs.length) {
    box.appendChild(el('p', 'text-xs text-slate-500', 'No recorded debates yet.'));
    return;
  }
  runs.forEach(r => {
    const row = el('div', 'flex items-center justify-between gap-2 p-2 rounded-lg bg-slate-900 border border-slate-800 hover:border-blue-500 cursor-pointer');
    const left = el('div', 'min-w-0');
    const head = el('div', 'text-xs font-bold text-slate-200',
      `Debate #${r.run_number} · ${new Date((r.finished_at || r.started_at) * 1000).toLocaleString()} · ${r.status || '?'}`);
    left.appendChild(head);
    const bits = [];
    if (r.verdict) bits.push('verdict: ' + r.verdict);
    if (r.scores) {
      const avg = ['novelty','feasibility','market_fit']
        .map(k => r.scores[k] && (r.scores[k].score !== undefined ? r.scores[k].score : r.scores[k]))
        .filter(v => v !== undefined && v !== null);
      if (avg.length) bits.push('avg score: ' + (avg.reduce((a, b) => a + b, 0) / avg.length).toFixed(1));
    }
    if (r.turns_used) bits.push(r.turns_used + ' turns');
    if (bits.length) left.appendChild(el('div', 'text-[11px] text-slate-400', bits.join(' · ')));
    if (r.comment) left.appendChild(el('div', 'text-[11px] text-amber-300/90 mt-0.5 break-words', '💬 ' + r.comment));
    row.appendChild(left);
    row.appendChild(el('span', 'text-xs text-blue-400 shrink-0', 'Replay ▸'));
    row.onclick = () => showRun(modalIdeaId, r.id);
    box.appendChild(row);
  });
}

let _currentRun = null;   // full run payload for export buttons

async function showRun(ideaId, runId, opts) {
  const res = await fetch(`/api/ideas/${ideaId}/runs/${runId}`);
  if (!res.ok) { alert('Could not load debate.'); return; }
  const run = await res.json();
  _currentRun = run;
  const replay = document.getElementById('idea-modal-replay');
  replay.classList.remove('hidden');
  document.getElementById('idea-modal-replay-title').textContent =
    `Debate #${run.run_number} — ${run.status || '?'}${run.comment ? ' · 💬 ' + run.comment : ''}`;

  buildRunTabs(run);
  showRunTab('timeline');

  // Export buttons
  const mdBtn = document.getElementById('idea-run-md-btn');
  const pdfBtn = document.getElementById('idea-run-pdf-btn');
  mdBtn.classList.remove('hidden');
  pdfBtn.classList.remove('hidden');
  mdBtn.onclick = () => downloadRunMarkdown(run);
  pdfBtn.onclick = () => printRunReport(run);

  // PRD / research brief toggles
  const doc = document.getElementById('idea-modal-doc');
  const prdBtn = document.getElementById('idea-modal-prd-btn');
  const briefBtn = document.getElementById('idea-modal-brief-btn');
  doc.classList.add('hidden');
  prdBtn.classList.toggle('hidden', !run.prd_text);
  briefBtn.classList.toggle('hidden', !run.research_brief);
  // Smart doc viewer: JSON → structured tree, markdown → formatted HTML,
  // anything else → plain text (same renderer as the live debate feed).
  const showDoc = (text, fallback) => {
    doc.classList.remove('md-body', 'whitespace-pre-wrap');
    doc.textContent = '';
    renderAgentOutput(doc, text || fallback);
    doc.classList.remove('hidden');
    window.scrollTo(0, 0);
  };
  prdBtn.onclick = () => showDoc(run.prd_text, '(no PRD)');
  briefBtn.onclick = () => showDoc(run.research_brief, '(no research brief)');
  if (opts && opts.showPrd && run.prd_text) prdBtn.click();
}

// ── Participant tabs inside a past debate ──────────────────────────────
function buildRunTabs(run) {
  const tabs = document.getElementById('run-tabs');
  tabs.textContent = '';
  const agents = [];
  (run.events || []).forEach(ev => {
    const a = ev.agent || '?';
    if (!agents.includes(a)) agents.push(a);
  });
  const mkTab = (id, label, count) => {
    const t = el('button',
      'px-3 py-1.5 text-xs font-bold rounded-t-lg rounded-b-lg bg-slate-900 border border-slate-800 hover:border-blue-500 text-slate-300',
      label + (count ? ` (${count})` : ''));
    t.dataset.tab = id;
    t.onclick = () => showRunTab(id);
    tabs.appendChild(t);
  };
  mkTab('timeline', '🕘 Timeline', (run.events || []).length);
  agents.forEach(a => mkTab('agent:' + a, a,
    run.events.filter(e2 => (e2.agent || '?') === a).length));
}

function showRunTab(tab) {
  // highlight active tab
  document.querySelectorAll('#run-tabs button').forEach(b => {
    b.className = b.className.replace(/border-blue-500 text-white/g, '').replace(/bg-slate-800/g, '') +
      (b.dataset.tab === tab ? ' !border-blue-500 !text-white !bg-slate-800' : '');
  });
  const feedBox = document.getElementById('idea-modal-feed');
  feedBox.textContent = '';
  const run = _currentRun;
  if (!run) return;

  if (tab === 'timeline') {
    // Chronological transcript — compact, structured rendering per message.
    (run.events || []).forEach(ev => {
      const div = el('div', 'msg p-4 rounded-lg bg-slate-900 border border-slate-800 text-sm');
      div.appendChild(el('span', 'font-bold text-yellow-400 text-xs', '[' + (ev.agent || '?') + ']'));
      const body = el('div', 'mt-1 text-slate-100 whitespace-pre-wrap break-words');
      renderAgentOutput(body, ev.text || '');
      div.appendChild(body);
      feedBox.appendChild(div);
    });
    if (!(run.events || []).length) {
      feedBox.appendChild(el('p', 'text-xs text-slate-500 p-2', 'No transcript recorded for this debate.'));
    }
  } else if (tab.startsWith('agent:')) {
    // Full-width detail pane with ALL outputs of this participant.
    const agent = tab.slice(6);
    (run.events || []).filter(e2 => (e2.agent || '?') === agent).forEach((ev, i, arr) => {
      const card = el('div', 'p-5 rounded-xl bg-slate-900 border border-slate-700 mb-4');
      card.appendChild(el('div', 'text-xs uppercase tracking-wider text-blue-400 font-bold mb-2',
        agent + (arr.length > 1 ? ` — output ${i + 1} of ${arr.length}` : ' — full output')));
      const body = el('div', 'text-sm text-slate-100 break-words');
      renderAgentOutput(body, ev.text || '');
      card.appendChild(body);
      feedBox.appendChild(card);
    });
  }
  document.getElementById('idea-modal-replay').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── Run report export (.md download + browser-print PDF) ──────────────
function buildRunMarkdown(run, ideaTitle) {
  const lines = [];
  lines.push(`# Debate #${run.run_number} — ${ideaTitle || ''}`);
  lines.push('');
  lines.push(`- **Status:** ${run.status || '?'}`);
  lines.push(`- **Verdict:** ${run.verdict || '—'}`);
  if (run.scores) {
    const sc = run.scores;
    const fmt = k => sc[k] && (sc[k].score !== undefined ? sc[k].score : sc[k]);
    lines.push(`- **Scores (out of 10):** novelty ${fmt('novelty') ?? '—'} · feasibility ${fmt('feasibility') ?? '—'} · market_fit ${fmt('market_fit') ?? '—'}`);
  }
  if (run.comment) lines.push(`- **Human comment:** ${run.comment}`);
  if (run.started_at) {
    const d = new Date(run.finished_at * 1000 || run.started_at * 1000);
    lines.push(`- **Date:** ${d.toLocaleString()}`);
  }
  if (run.turns_used) lines.push(`- **Orchestrator turns:** ${run.turns_used}`);
  lines.push('');
  if (run.prd_text) {
    lines.push('## PRD', '', run.prd_text, '');
  }
  if (run.research_brief) {
    lines.push('## Research Brief', '', formatDocText(run.research_brief), '');
  }
  lines.push('## Transcript', '');
  (run.events || []).forEach(ev => {
    lines.push(`### [${ev.agent || '?'}]`, '', ev.text || '', '');
  });
  return lines.join('\n');
}

function downloadRunMarkdown(run) {
  const md = buildRunMarkdown(run, modalIdea && modalIdea.title);
  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `debate-${run.run_number}-${(modalIdea && modalIdea.title || 'idea').replace(/[^a-z0-9]+/gi, '-').toLowerCase().slice(0, 50)}.md`;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function printRunReport(run) {
  // Open the report in a clean window and trigger the browser's print-to-PDF.
  const w = window.open('', '_blank');
  if (!w) { alert('Pop-up blocked — allow pop-ups to print.'); return; }
  const esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
  // Formatted block: markdown (or JSON→markdown) → sanitized HTML when the
  // renderer is available; falls back to escaped <pre> offline.
  const fmtBlock = text => {
    if (!text) return '';
    if (window.marked && window.DOMPurify) {
      try {
        return `<div class="fmt">${DOMPurify.sanitize(marked.parse(formatDocText(text)), { ALLOWED_ATTR: ['href', 'title', 'alt'] })}</div>`;
      } catch (_) { /* fall through */ }
    }
    return `<pre>${esc(formatDocText(text))}</pre>`;
  };
  const bodyHtml = `
    <h1>Debate #${run.run_number}${modalIdea ? ' — ' + esc(modalIdea.title) : ''}</h1>
    <p><b>Status:</b> ${esc(run.status)} &nbsp; <b>Verdict:</b> ${esc(run.verdict || '—')}</p>
    ${run.prd_text ? `<h2>PRD</h2>${fmtBlock(run.prd_text)}` : ''}
    ${run.research_brief ? `<h2>Research Brief</h2>${fmtBlock(run.research_brief)}` : ''}
    <h2>Transcript</h2>
    ${(run.events || []).map(ev => `<h3>[${esc(ev.agent)}]</h3>${fmtBlock(ev.text)}`).join('')}`;
  w.document.write(`<!doctype html><html><head><title>Debate report</title>
    <style>body{font-family:system-ui,sans-serif;max-width:800px;margin:2rem auto;padding:0 1rem;color:#111}
    pre{white-space:pre-wrap;background:#f5f5f5;padding:.75rem;border-radius:6px;font-size:.8rem}
    h1{border-bottom:2px solid #333}h2{border-bottom:1px solid #ccc;margin-top:2rem}
    h3{margin-bottom:.25rem;color:#555}
    .fmt ul,.fmt ol{padding-left:1.4em}.fmt li{margin:.2em 0}
    .fmt table{border-collapse:collapse;margin:.5em 0}.fmt th,.fmt td{border:1px solid #999;padding:.25em .5em}
    .fmt a{color:#0366d6}.fmt code{background:#f0f0f0;padding:0 .25em;border-radius:3px}
    .fmt pre{background:#f5f5f5}</style></head><body>${bodyHtml}</body></html>`);
  w.document.close();
  // CSP note (G2): the popup must not carry an inline <script> — under a
  // strict script-src 'self' policy it would be blocked. Same-origin opener
  // may call print() on it directly.
  setTimeout(() => { try { w.focus(); w.print(); } catch (_) {} }, 350);
}

function renderModalFooter(idea) {
  const footer = document.getElementById('idea-modal-footer');
  footer.textContent = '';

  const resumeBtn = el('button', 'px-3 py-1.5 text-xs font-bold rounded-lg bg-blue-600 hover:bg-blue-500 text-white', '▶ Resume debate');
  resumeBtn.onclick = async () => {
    const comment = document.getElementById('idea-resume-comment').value.trim();
    const res = await fetch(`/api/ideas/${modalIdeaId}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ comment }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert('Resume failed: ' + (err.detail || res.status));
      return;
    }
    closeIdeaModal();
    addMessage('Human', comment ? '↻ Resumed with comment: ' + comment : '↻ Resumed debate');
    document.getElementById('debate-feed').scrollIntoView({ behavior: 'smooth' });
    fetchState();
  };
  footer.appendChild(resumeBtn);

  const expBtn = el('button', 'px-3 py-1.5 text-xs font-bold rounded-lg bg-slate-700 hover:bg-slate-600 text-white', '⬇ Export JSON');
  expBtn.onclick = () => { window.location.href = `/api/ideas/${modalIdeaId}/export`; };
  footer.appendChild(expBtn);

  if (idea.status !== 'PARK') {
    const parkBtn = el('button', 'px-3 py-1.5 text-xs font-bold rounded-lg bg-slate-700 hover:bg-slate-600 text-white', 'Park');
    parkBtn.onclick = async () => {
      await fetch(`/api/ideas/${modalIdeaId}/archive`, { method: 'POST' });
      closeIdeaModal();
      fetchIdeas();
      fetchFacets();
    };
    footer.appendChild(parkBtn);
  }

  const delBtn = el('button', 'px-3 py-1.5 text-xs font-bold rounded-lg bg-red-700/60 hover:bg-red-600 text-white', 'Delete');
  delBtn.onclick = async () => {
    if (!confirm(`Delete "${idea.title}" and all its debate history? This cannot be undone.`)) return;
    await fetch(`/api/ideas/${modalIdeaId}`, { method: 'DELETE' });
    closeIdeaModal();
    fetchIdeas();
    fetchFacets();
  };
  footer.appendChild(delBtn);
}

// Close the modal on backdrop click or Escape.
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeIdeaModal(); });

async function fetchCheckpoints() {
  const res = await fetch('/api/checkpoints');
  if (res.status === 401) return;
  const d = await res.json();
  const box = document.getElementById('in-progress-box');
  const list = document.getElementById('checkpoint-list');
  const cps = d.checkpoints || [];
  box.classList.toggle('hidden', cps.length === 0);
  list.innerHTML = '';
  cps.forEach(cp => {
    const row = el('div', 'flex items-center justify-between');
    row.appendChild(el('span', 'text-xs text-slate-300', `${cp.idea} — phase: ${cp.phase}`));
    const resumeBtn = el('button', 'px-2 py-1 text-[11px] rounded bg-amber-600 hover:bg-amber-500 text-white', 'Resume');
    resumeBtn.onclick = async () => {
      await fetch(`/api/checkpoints/${cp.run_id}/resume`, {method:'POST'});
      addMessage('System', 'Resuming checkpoint ' + cp.run_id + '...');
    };
    row.appendChild(resumeBtn);
    list.appendChild(row);
  });
}

// ── Self-improvement console (M3) ─────────────────────────────────────
async function fetchMemories() {
  const res = await fetch('/api/memories');
  if (res.status === 401) return;
  const d = await res.json();
  const tList = document.getElementById('techniques-list');
  const lList = document.getElementById('lessons-list');
  const iList = document.getElementById('idea-tree-list');
  tList.innerHTML = ''; lList.innerHTML = ''; iList.innerHTML = '';
  (d.techniques || []).forEach(t => {
    const li = el('li', '', `${t.name} (✓${t.success_count} ✗${t.failure_count})`);
    tList.appendChild(li);
  });
  (d.lessons || []).slice(0, 10).forEach(l => {
    lList.appendChild(el('li', '', `- ${l.name}: ${l.rule}`));
  });
  (d.idea_tree || []).slice(0, 20).forEach(i => {
    const dot = {ACTIVE:'🟢', PARK:'🟡', PRUNED:'🔴'}[i.status] || '⚪';
    iList.appendChild(el('li', '', `${dot} ${i.title} (${i.status})`));
  });
}

async function doDreamReview() {
  const res = await fetch('/scheduler/dream-review', {method: 'POST'});
  const d = await res.json();
  addMessage('System', '🧠 Dream review: ' + JSON.stringify(d));
  fetchMemories();
}

window.onload = function() { showApp({ email: 'local' }); };

// ── Event wiring (CSP-safe) ──────────────────────────────────────────
// G2/G3: inline onclick/onchange/oninput attributes are blocked under a
// strict script-src CSP. All handlers live here, attached by id or via the
// data-action delegation below.
(function wireEvents() {
  const $ = (id) => document.getElementById(id);
  const on = (idOrEl, ev, fn) => {
    const el = typeof idOrEl === 'string' ? $(idOrEl) : idOrEl;
    if (el) el.addEventListener(ev, fn);
  };
  on('usage-toggle', 'click', () => toggleUsage());
  on('btn-stop', 'click', () => doStop());
  on('btn-run', 'click', () => doRun());
  on('idea-input', 'keydown', (e) => { if (e.key === 'Enter') doRun(); });
  on('steering-input', 'keydown', (e) => { if (e.key === 'Enter') doSteering(); });
  on('btn-steering', 'click', () => doSteering());
  on('btn-rebut', 'click', () => submitRebut());
  on('ideas-search', 'input', () => fetchIdeas());
  on('ideas-import-file', 'change', (e) => importIdeas(e.target));
  on('btn-import-ideas', 'click', () => { const f = $('ideas-import-file'); if (f) f.click(); });
  on('btn-export-json', 'click', () => exportAllIdeas());
  on('btn-export-csv', 'click', () => exportIdeas());
  on('btn-dream-review', 'click', () => doDreamReview());

  // Delegated clicks for repeated/parameterised buttons.
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    switch (btn.dataset.action) {
      case 'toggle-usage': toggleUsage(); break;
      case 'usage-period': fetchUsage(btn.dataset.period); break;
      case 'verdict': verdictAction(btn.dataset.verdict); break;
      case 'prd': prdAction(btn.dataset.prd); break;
      case 'close-idea-modal': closeIdeaModal(); break;
    }
  });
})();
setInterval(fetchState, 3000);
setInterval(fetchMemories, 10000);
setInterval(fetchIdeas, 15000);
setInterval(fetchFacets, 15000);
setInterval(fetchCheckpoints, 8000);
