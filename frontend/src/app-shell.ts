/**
 * app-shell.ts — Idea Lint UI Shell & Idea Workspace Modal (T6/T7).
 */
import * as store from './store';
import { renderMarkdown } from './debate';
import type { Idea, IdeaRun } from './idb';
import { dom } from './dom';

export interface ShellOptions {
  onRun?: (idea: Idea, options?: { comment?: string; urls?: string[] }) => void;
}

let ideas: Idea[] = [];
let onRun: ((idea: Idea, options?: { comment?: string; urls?: string[] }) => void) | null = null;
let activeFilter = 'ALL';
let searchQuery = '';

// Active modal state
let modalIdea: Idea | null = null;
let activeRun: IdeaRun | null = null;
let activeTab = 'prd';
let transcriptAgentFilter = 'ALL';

// ── Rendering Helpers ────────────────────────────────────────────────

function getStatusBadge(status?: string, verdict?: string): HTMLElement {
  const st = (status || verdict || 'ACTIVE').toUpperCase();
  let bg = 'bg-slate-800 text-slate-400 border-slate-700';
  if (st.includes('PROCEED')) bg = 'bg-emerald-950/80 text-emerald-300 border-emerald-600/70';
  else if (st.includes('PARK')) bg = 'bg-amber-950/80 text-amber-300 border-amber-600/70';
  else if (st.includes('PRUNE')) bg = 'bg-rose-950/80 text-rose-300 border-rose-600/70';
  else if (st.includes('RUNNING')) bg = 'bg-blue-950/80 text-blue-300 border-blue-600/70';

  return dom('span', `px-2 py-0.5 rounded text-[11px] font-bold border uppercase tracking-wider ${bg}`, st);
}

function renderRow(idea: Idea): HTMLElement {
  const row = dom(
    'div',
    'idea-row p-3.5 rounded-xl bg-slate-900/80 border border-slate-800 hover:border-slate-700 transition-all cursor-pointer flex flex-col md:flex-row md:items-center justify-between gap-3',
  );

  const left = dom('div', 'flex-1 min-w-0');
  const titleRow = dom('div', 'flex items-center gap-2 flex-wrap mb-1');
  titleRow.appendChild(getStatusBadge(idea.status, idea.verdict));

  const title = dom('span', 'font-semibold text-slate-100 text-sm hover:text-blue-400 transition-colors', idea.title);
  titleRow.appendChild(title);
  left.appendChild(titleRow);

  const meta = dom('div', 'text-xs text-slate-400 flex items-center gap-3');
  const d = new Date(idea.createdAt).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
  meta.appendChild(dom('span', '', `Added: ${d}`));
  if (idea.runs && idea.runs.length > 0) {
    meta.appendChild(dom('span', 'text-blue-400 font-medium', `${idea.runs.length} debate${idea.runs.length > 1 ? 's' : ''}`));
  }
  if (idea.scores) {
    const sc = idea.scores;
    const n = typeof sc.novelty === 'object' && sc.novelty !== null ? (sc.novelty as any).score : sc.novelty;
    const f = typeof sc.feasibility === 'object' && sc.feasibility !== null ? (sc.feasibility as any).score : sc.feasibility;
    const m = typeof sc.market_fit === 'object' && sc.market_fit !== null ? (sc.market_fit as any).score : sc.market_fit;
    meta.appendChild(dom('span', 'text-emerald-400 font-mono font-medium', `N:${n ?? '—'} F:${f ?? '—'} M:${m ?? '—'}`));
  }
  left.appendChild(meta);
  row.appendChild(left);

  // Click on main area opens modal
  left.addEventListener('click', (e) => {
    e.stopPropagation();
    openIdeaModal(idea);
  });

  const actions = dom('div', 'flex items-center gap-2 shrink-0');

  if (onRun) {
    const runBtn = dom('button', 'px-3 py-1.5 text-xs font-bold rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white transition-colors shadow-sm flex items-center gap-1', '▶ Debate');
    runBtn.setAttribute('data-run-id', idea.id);
    runBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      onRun?.(idea);
    });
    actions.appendChild(runBtn);
  }

  const detailBtn = dom('button', 'px-2.5 py-1.5 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors', 'Details');
  detailBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    openIdeaModal(idea);
  });
  actions.appendChild(detailBtn);

  const delBtn = dom('button', 'px-2.5 py-1.5 text-xs rounded-lg bg-slate-800/80 hover:bg-rose-900/60 hover:text-rose-200 text-slate-400 transition-colors', '✕');
  delBtn.setAttribute('data-id', idea.id);
  delBtn.title = 'Delete idea';
  delBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (confirm(`Delete idea: "${idea.title}"?`)) {
      void removeIdea(idea.id);
    }
  });
  actions.appendChild(delBtn);

  row.appendChild(actions);
  return row;
}

function filterIdeas(): Idea[] {
  return ideas.filter((i) => {
    if (activeFilter !== 'ALL') {
      const st = (i.status || i.verdict || 'ACTIVE').toUpperCase();
      if (activeFilter === 'PROCEED' && !st.includes('PROCEED')) return false;
      if (activeFilter === 'PARK' && !st.includes('PARK')) return false;
      if (activeFilter === 'PRUNE' && !st.includes('PRUNE')) return false;
      if (activeFilter === 'ACTIVE' && (st.includes('PROCEED') || st.includes('PARK') || st.includes('PRUNE'))) return false;
    }
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      const matchTitle = i.title.toLowerCase().includes(q);
      const matchPrd = i.prd_text?.toLowerCase().includes(q);
      if (!matchTitle && !matchPrd) return false;
    }
    return true;
  });
}

function renderList(): void {
  const list = document.getElementById('ideas-list');
  if (!list) return;
  list.innerHTML = '';

  const filtered = filterIdeas();
  for (const idea of filtered) {
    list.appendChild(renderRow(idea));
  }

  const countEl = document.getElementById('idea-count');
  if (countEl) {
    countEl.textContent = `${filtered.length} of ${ideas.length} idea${ideas.length === 1 ? '' : 's'}`;
  }

  const empty = document.getElementById('ideas-empty');
  if (empty) {
    empty.classList.toggle('hidden', filtered.length > 0);
  }

  renderFacets();
}

function renderFacets(): void {
  const container = document.getElementById('facet-statuses');
  if (!container) return;
  container.innerHTML = '';

  const counts: Record<string, number> = { ALL: ideas.length, ACTIVE: 0, PROCEED: 0, PARK: 0, PRUNE: 0 };
  for (const i of ideas) {
    const st = (i.status || i.verdict || 'ACTIVE').toUpperCase();
    if (st.includes('PROCEED')) counts.PROCEED++;
    else if (st.includes('PARK')) counts.PARK++;
    else if (st.includes('PRUNE')) counts.PRUNE++;
    else counts.ACTIVE++;
  }

  const facets = [
    { key: 'ALL', label: 'All Ideas', count: counts.ALL },
    { key: 'ACTIVE', label: 'In Progress / New', count: counts.ACTIVE },
    { key: 'PROCEED', label: 'Proceed (Approved)', count: counts.PROCEED },
    { key: 'PARK', label: 'Parked', count: counts.PARK },
    { key: 'PRUNE', label: 'Pruned', count: counts.PRUNE },
  ];

  for (const f of facets) {
    const btn = dom(
      'button',
      `w-full text-left px-3 py-2 rounded-lg text-xs font-semibold flex items-center justify-between transition-colors ${
        activeFilter === f.key ? 'bg-blue-600/20 text-blue-400 border border-blue-500/40' : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
      }`,
    );
    btn.appendChild(dom('span', '', f.label));
    btn.appendChild(dom('span', 'text-[11px] px-1.5 py-0.5 rounded-full bg-slate-800/80 text-slate-400', String(f.count)));
    btn.addEventListener('click', () => {
      activeFilter = f.key;
      renderList();
    });
    container.appendChild(btn);
  }
}

// ── Idea Workspace Modal ─────────────────────────────────────────────

export function openIdeaModal(idea: Idea): void {
  modalIdea = idea;
  const modal = document.getElementById('idea-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  document.body.style.overflow = 'hidden';

  activeRun = (idea.runs && idea.runs.length > 0) ? idea.runs[idea.runs.length - 1] : null;
  activeTab = 'prd';
  transcriptAgentFilter = 'ALL';

  renderModalContent();
}

export function closeIdeaModal(): void {
  const modal = document.getElementById('idea-modal');
  if (!modal) return;
  modal.classList.add('hidden');
  document.body.style.overflow = '';
  modalIdea = null;
  activeRun = null;
}

function renderModalContent(): void {
  if (!modalIdea) return;

  const titleWrap = document.getElementById('idea-modal-title-wrap');
  if (titleWrap) {
    titleWrap.innerHTML = '';
    const h = dom('h2', 'text-2xl font-black text-white leading-tight', modalIdea.title);
    titleWrap.appendChild(h);
  }

  const meta = document.getElementById('idea-modal-meta');
  if (meta) {
    const d = new Date(modalIdea.createdAt).toLocaleString();
    meta.innerHTML = `<span class="text-slate-400">Created: ${d}</span> · <span class="font-semibold text-blue-400">${(modalIdea.runs || []).length} debate run(s)</span>`;
  }

  const pitch = document.getElementById('idea-modal-pitch');
  if (pitch) {
    pitch.innerHTML = '';
    if (modalIdea.urls && modalIdea.urls.length > 0) {
      const urlsEl = dom('div', 'text-xs text-cyan-400 mt-1');
      urlsEl.innerHTML = `<strong>Research URLs:</strong> ${modalIdea.urls.join(', ')}`;
      pitch.appendChild(urlsEl);
    }
  }

  // Runs chips / list
  const runsContainer = document.getElementById('idea-modal-runs');
  if (runsContainer) {
    runsContainer.innerHTML = '';
    const runs = modalIdea.runs || [];
    if (runs.length === 0) {
      runsContainer.innerHTML = `<p class="text-xs text-slate-500 italic">No debate has been executed for this idea yet. Start one from the main screen.</p>`;
    } else {
      const row = dom('div', 'flex flex-wrap gap-2');
      for (const r of runs) {
        const isSelected = activeRun?.run_id === r.run_id;
        const btn = dom(
          'button',
          `px-3 py-1.5 rounded-lg text-xs font-bold transition-all border ${
            isSelected
              ? 'bg-blue-600 text-white border-blue-400'
              : 'bg-slate-900 text-slate-400 border-slate-700 hover:text-white'
          }`,
          `Run #${r.run_number || 1} · ${r.verdict || r.status}`,
        );
        btn.addEventListener('click', () => {
          activeRun = r;
          renderModalContent();
        });
        row.appendChild(btn);
      }
      runsContainer.appendChild(row);
    }
  }

  // Render detail pane
  renderModalDetail();
  renderModalFooter();
}

function renderModalDetail(): void {
  const replay = document.getElementById('idea-modal-replay');
  if (!replay) return;

  if (!activeRun) {
    replay.classList.add('hidden');
    return;
  }
  replay.classList.remove('hidden');

  const title = document.getElementById('idea-modal-replay-title');
  if (title) {
    title.textContent = `Debate Run #${activeRun.run_number || 1} — ${activeRun.verdict || activeRun.status}`;
  }

  // Wire buttons
  const resumeTopBtn = document.getElementById('idea-run-resume-btn') as HTMLButtonElement | null;
  if (resumeTopBtn) {
    resumeTopBtn.onclick = () => {
      if (modalIdea && onRun) {
        const commentInput = document.getElementById('modal-resume-comment') as HTMLTextAreaElement | null;
        const comment = commentInput?.value.trim() || undefined;
        const targetIdea = modalIdea;
        closeIdeaModal();
        onRun(targetIdea, { comment });
      }
    };
  }

  const mdBtn = document.getElementById('idea-run-md-btn') as HTMLButtonElement | null;
  if (mdBtn) {
    mdBtn.classList.remove('hidden');
    mdBtn.onclick = () => {
      if (modalIdea && activeRun) downloadRunMarkdown(modalIdea, activeRun);
    };
  }

  const pdfBtn = document.getElementById('idea-run-pdf-btn') as HTMLButtonElement | null;
  if (pdfBtn) {
    pdfBtn.classList.remove('hidden');
    pdfBtn.onclick = () => {
      if (modalIdea && activeRun) printRunReport(modalIdea, activeRun);
    };
  }

  // Tabs
  const tabsRow = document.getElementById('run-tabs');
  if (tabsRow) {
    tabsRow.innerHTML = '';
    const tabs: Array<{ id: string; label: string; count?: number }> = [
      { id: 'prd', label: '📄 PRD' },
      { id: 'research', label: '🔍 Research Brief' },
      { id: 'advocate', label: '⚖️ Advocate' },
      { id: 'critic', label: '🛡️ Critic' },
      { id: 'creative', label: '💡 Creative Angles' },
      { id: 'transcript', label: '💬 Live Transcript', count: activeRun.events?.length },
    ];

    for (const t of tabs) {
      const btn = dom(
        'button',
        `px-3 py-1.5 text-xs font-bold rounded-t-lg transition-colors border-b-2 ${
          activeTab === t.id
            ? 'text-blue-400 border-blue-500 bg-slate-900'
            : 'text-slate-400 border-transparent hover:text-slate-200'
        }`,
        t.count !== undefined ? `${t.label} (${t.count})` : t.label,
      );
      btn.addEventListener('click', () => {
        activeTab = t.id;
        renderModalDetail();
      });
      tabsRow.appendChild(btn);
    }
  }

  // Detail content
  const content = document.getElementById('run-detail-content');
  if (content) {
    content.innerHTML = '';
    if (activeTab === 'prd') {
      content.innerHTML = renderMarkdown(activeRun.prd_text || '_No PRD generated for this run._');
    } else if (activeTab === 'research') {
      content.innerHTML = renderMarkdown(activeRun.research_brief || '_No research brief available._');
    } else if (activeTab === 'advocate') {
      content.innerHTML = renderMarkdown(activeRun.advocate_argument || '_No advocate argument recorded._');
    } else if (activeTab === 'critic') {
      content.innerHTML = renderMarkdown(activeRun.critic_rebuttal || '_No critic rebuttal recorded._');
    } else if (activeTab === 'creative') {
      content.innerHTML = renderMarkdown(activeRun.creative_angles || '_No creative angles recorded._');
    } else if (activeTab === 'transcript') {
      renderTranscriptTab(content);
    }
  }
}

function renderTranscriptTab(container: HTMLElement): void {
  if (!activeRun || !activeRun.events || activeRun.events.length === 0) {
    container.innerHTML = `<p class="text-xs text-slate-500 italic">No transcript recorded for this run.</p>`;
    return;
  }

  const agents = Array.from(new Set(activeRun.events.map((e) => e.agent).filter(Boolean)));
  const filterRow = dom('div', 'flex flex-wrap gap-1.5 mb-4 pb-2 border-b border-slate-800');

  const allBtn = dom(
    'button',
    `px-2.5 py-1 rounded text-xs font-semibold ${
      transcriptAgentFilter === 'ALL' ? 'bg-blue-600 text-white' : 'bg-slate-900 text-slate-400 hover:text-white'
    }`,
    'All Messages',
  );
  allBtn.addEventListener('click', () => {
    transcriptAgentFilter = 'ALL';
    renderTranscriptTab(container);
  });
  filterRow.appendChild(allBtn);

  for (const ag of agents) {
    const btn = dom(
      'button',
      `px-2.5 py-1 rounded text-xs font-semibold ${
        transcriptAgentFilter === ag ? 'bg-blue-600 text-white' : 'bg-slate-900 text-slate-400 hover:text-white'
      }`,
      ag!,
    );
    btn.addEventListener('click', () => {
      transcriptAgentFilter = ag!;
      renderTranscriptTab(container);
    });
    filterRow.appendChild(btn);
  }

  container.innerHTML = '';
  container.appendChild(filterRow);

  const feed = dom('div', 'space-y-3');
  const filteredEvents = activeRun.events.filter((e) =>
    transcriptAgentFilter === 'ALL' ? true : e.agent === transcriptAgentFilter,
  );

  for (const ev of filteredEvents) {
    const card = dom('div', 'p-3 rounded-xl bg-slate-900/90 border border-slate-800');
    card.appendChild(dom('div', 'text-xs font-bold text-blue-400 mb-1', ev.agent || 'System'));
    const body = dom('div', 'md-body text-sm text-slate-200');
    body.innerHTML = renderMarkdown(ev.text || '');
    card.appendChild(body);
    feed.appendChild(card);
  }
  container.appendChild(feed);
}

export function setOnRun(fn: (idea: Idea, options?: { comment?: string; urls?: string[] }) => void): void {
  onRun = fn;
}

function renderModalFooter(): void {
  const footer = document.getElementById('idea-modal-footer');
  if (!footer || !modalIdea) return;
  footer.innerHTML = '';

  const wrap = dom('div', 'w-full flex flex-col gap-3');

  // Add guidance comment input for the next debate run
  const commentBox = dom('div', 'w-full bg-slate-950/80 border border-slate-800 rounded-xl p-3');
  const commentLabel = dom('label', 'block text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-1.5 flex items-center justify-between');
  commentLabel.innerHTML = `<span>💬 Next Debate Direction / Feedback</span> <span class="text-[10px] text-slate-500 font-normal">Injected as priority human guidance</span>`;
  commentBox.appendChild(commentLabel);

  const commentInput = dom('textarea', 'w-full bg-slate-900 border border-slate-700 focus:border-blue-500 rounded-lg p-2.5 text-xs text-slate-100 placeholder-slate-500 outline-none transition-colors') as HTMLTextAreaElement;
  commentInput.id = 'modal-resume-comment';
  commentInput.rows = 2;
  commentInput.placeholder = "E.g. [Approve]: adopt the hybrid architecture and generate cursor-optimized PRD; or propose new pivot directions...";
  commentBox.appendChild(commentInput);

  // Quick preset pills
  const pills = dom('div', 'flex gap-2 mt-2 flex-wrap');
  const presets = [
    { label: '✅ [Approve]: Generate PRD with mitigations', val: '[Approve]: We adopt the proposed architecture and developer pivot. Proceed with PRD generation and Security Audit.' },
    { label: '✏️ [Changes]: Focus on AI coders', val: '[Changes]: Focus exclusively on generating .cursorrules and PRD.md for vibe-coding workflows.' },
  ];
  for (const pr of presets) {
    const pill = dom('button', 'px-2.5 py-1 text-[11px] font-semibold bg-slate-800 hover:bg-slate-700 text-blue-300 rounded-md border border-slate-700 transition-colors', pr.label);
    pill.onclick = (e) => {
      e.preventDefault();
      commentInput.value = pr.val;
      commentInput.focus();
    };
    pills.appendChild(pill);
  }
  commentBox.appendChild(pills);
  wrap.appendChild(commentBox);

  const btnRow = dom('div', 'flex items-center justify-between gap-3 flex-wrap');

  const leftBtns = dom('div', 'flex items-center gap-2');
  const resumeBtn = dom(
    'button',
    'px-4 py-2 text-xs font-bold rounded-lg bg-blue-600 hover:bg-blue-500 text-white shadow-sm transition-colors flex items-center gap-1.5',
    '▶ Re-run / Resume Debate',
  );
  resumeBtn.onclick = () => {
    if (modalIdea && onRun) {
      const comment = commentInput.value.trim() || undefined;
      const targetIdea = modalIdea;
      closeIdeaModal();
      onRun(targetIdea, { comment });
    }
  };
  leftBtns.appendChild(resumeBtn);

  const expBtn = dom(
    'button',
    'px-3 py-2 text-xs font-bold rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 transition-colors',
    '⬇ Export Markdown',
  );
  expBtn.onclick = () => {
    if (modalIdea && activeRun) downloadRunMarkdown(modalIdea, activeRun);
  };
  leftBtns.appendChild(expBtn);

  btnRow.appendChild(leftBtns);

  const delBtn = dom(
    'button',
    'px-3 py-2 text-xs font-bold rounded-lg bg-rose-950/80 hover:bg-rose-900 border border-rose-800/80 text-rose-200 transition-colors',
    'Delete Idea',
  );
  delBtn.onclick = async () => {
    if (confirm(`Delete "${modalIdea!.title}" and all its history?`)) {
      await store.removeIdea(modalIdea!.id);
      closeIdeaModal();
      await refresh();
    }
  };
  btnRow.appendChild(delBtn);
  wrap.appendChild(btnRow);

  footer.appendChild(wrap);
}

function downloadRunMarkdown(idea: Idea, run: IdeaRun): void {
  const md = store.buildRunMarkdown(idea, run);
  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  const cleanTitle = (idea.title || 'idea').replace(/[^a-z0-9]+/gi, '-').toLowerCase().slice(0, 40);
  a.download = `idealint-debate-${cleanTitle}-${Date.now()}.md`;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function printRunReport(idea: Idea, run: IdeaRun): void {
  const w = window.open('', '_blank');
  if (!w) {
    alert('Pop-up blocked — allow pop-ups to print PDF.');
    return;
  }
  const bodyHtml = `
    <div class="no-print" style="margin-bottom: 1.5rem; display: flex; justify-content: space-between; align-items: center; background: #f1f5f9; padding: 10px 16px; border-radius: 8px; border: 1px solid #cbd5e1;">
      <button onclick="window.print()" style="padding: 8px 18px; background: #2563eb; color: #fff; border: none; border-radius: 6px; font-weight: bold; font-size: 13px; cursor: pointer; display: flex; items-center; gap: 6px;">🖨️ Print / Save as PDF</button>
      <span style="color: #64748b; font-size: 12px;">Tip: Select <strong>"Save as PDF"</strong> in your browser print dialogue.</span>
    </div>
    <h1>Idea Lint Debate: ${idea.title}</h1>
    <p><b>Status:</b> ${run.status || 'DONE'} &nbsp; <b>Verdict:</b> ${run.verdict || '—'}</p>
    ${run.prd_text ? `<h2>Product Requirements Document (PRD)</h2><div class="fmt">${renderMarkdown(run.prd_text)}</div>` : ''}
    ${run.research_brief ? `<h2>Research Brief</h2><div class="fmt">${renderMarkdown(run.research_brief)}</div>` : ''}
    <h2>Debate Transcript</h2>
    ${(run.events || []).map((e) => `<h3>[${e.agent || 'System'}]</h3><div class="fmt">${renderMarkdown(e.text || '')}</div>`).join('')}
  `;

  w.document.write(`<!doctype html><html><head><title>Idea Lint Debate Report — ${idea.title.slice(0, 30)}</title>
    <style>
      body{font-family:system-ui,sans-serif;max-width:850px;margin:2rem auto;padding:0 1.5rem;color:#0f172a;line-height:1.6}
      h1{border-bottom:2px solid #0f172a;padding-bottom:.5rem;font-size:1.6rem}
      h2{border-bottom:1px solid #cbd5e1;margin-top:2rem;padding-bottom:.3rem;color:#1e293b;font-size:1.3rem}
      h3{margin-bottom:.25rem;color:#475569;font-size:1rem;margin-top:1.2rem}
      .fmt ul,.fmt ol{padding-left:1.4em}.fmt li{margin:.25em 0}
      .fmt table{border-collapse:collapse;margin:.75em 0;width:100%}.fmt th,.fmt td{border:1px solid #cbd5e1;padding:.4em .6em;text-align:left}
      .fmt th{background:#f8fafc}
      .fmt a{color:#2563eb;text-decoration:underline}
      .fmt code{background:#f1f5f9;padding:0.1em .3em;border-radius:4px;font-family:monospace;font-size:0.9em}
      .fmt pre{background:#f8fafc;padding:.85rem;border-radius:6px;border:1px solid #e2e8f0;white-space:pre-wrap;font-size:0.85em}
      @media print {
        .no-print { display: none !important; }
        body { max-width: 100%; margin: 0; padding: 0; }
      }
    </style></head><body>${bodyHtml}
    <script>
      setTimeout(() => { window.print(); }, 250);
    <\/script></body></html>`);
  w.document.close();
}

// ── Shell Actions ────────────────────────────────────────────────────

export async function addIdea(): Promise<void> {
  const input = document.getElementById('idea-input') as HTMLInputElement | null;
  const urlInput = document.getElementById('url-input') as HTMLInputElement | null;
  const status = document.getElementById('status-line');
  const hint = document.getElementById('duplicate-hint');

  if (!input) return;
  const text = input.value.trim();
  const rawUrls = urlInput ? urlInput.value.trim() : '';
  const urls = rawUrls ? rawUrls.split(',').map((u) => u.trim()).filter(Boolean) : [];

  if (hint) hint.classList.add('hidden');
  if (!text) return;

  const outcome = await store.addIdea(text, urls);
  if (outcome.reason === 'duplicate') {
    if (hint) {
      hint.textContent = `Duplicate: an idea with this exact title ("${outcome.existingTitle ?? ''}") already exists.`;
      hint.classList.remove('hidden');
    }
    return;
  }
  if (outcome.reason === 'empty') return;

  input.value = '';
  if (urlInput) urlInput.value = '';
  if (status) status.textContent = `📝 Saved "${text}"`;
  await refresh();

  if (outcome.idea && onRun) {
    onRun(outcome.idea);
  }
}

export async function refresh(): Promise<void> {
  ideas = await store.listIdeas();
  renderList();
}

export async function removeIdea(id: string): Promise<void> {
  await store.removeIdea(id);
  await refresh();
}

function exportJson(): void {
  store.exportJson().then((blob) => {
    const a = document.createElement('a');
    const url = URL.createObjectURL(new Blob([blob], { type: 'application/json' }));
    a.href = url;
    a.download = `idealint-ideas-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  });
}

function exportCsv(): void {
  store.exportCsv().then((csv) => {
    const a = document.createElement('a');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    a.href = url;
    a.download = `idealint-ideas-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  });
}

async function onImportFile(file: File): Promise<void> {
  try {
    const text = await file.text();
    const summary = await store.importJson(text);
    const msg = `Import complete: ${summary.added} idea${summary.added === 1 ? '' : 's'} added, ${summary.existing} already present.`;
    const status = document.getElementById('status-line');
    if (status) status.textContent = msg;
    alert(msg);
    await refresh();
  } catch (err) {
    alert(`Import failed: ${String(err)}`);
  }
}

function wire(): void {
  document.getElementById('btn-add')?.addEventListener('click', () => void addIdea());
  document.getElementById('idea-input')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') void addIdea();
  });
  document.getElementById('url-input')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') void addIdea();
  });

  const search = document.getElementById('ideas-search') as HTMLInputElement | null;
  if (search) {
    search.addEventListener('input', () => {
      searchQuery = search.value.trim();
      renderList();
    });
  }

  document.getElementById('btn-export')?.addEventListener('click', exportJson);
  document.getElementById('btn-export-csv')?.addEventListener('click', exportCsv);
  document.getElementById('btn-import')?.addEventListener('click', () => {
    (document.getElementById('import-file') as HTMLInputElement | null)?.click();
  });

  document.getElementById('import-file')?.addEventListener('change', async (e) => {
    const f = (e.target as HTMLInputElement).files?.[0];
    if (f) await onImportFile(f);
    (e.target as HTMLInputElement).value = '';
  });

  document.getElementById('btn-clear')?.addEventListener('click', async () => {
    if (confirm('Delete ALL ideas stored in this browser? This cannot be undone.')) {
      await store.wipe();
      await refresh();
    }
  });

  document.getElementById('btn-close-idea-modal')?.addEventListener('click', closeIdeaModal);
  document.getElementById('btn-close-idea-modal-x')?.addEventListener('click', closeIdeaModal);

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeIdeaModal();
  });
}

export function init(options?: ShellOptions): void {
  onRun = options?.onRun ?? null;
  wire();
  void refresh();
}