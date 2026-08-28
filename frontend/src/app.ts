/**
 * app.ts — Main Entry & Composition Root for Idea Lint (T6/T7/T8).
 */
import * as shell from './app-shell';
import * as debate from './debate';
import * as byok from './byok';
import type { Idea } from './idb';

// ── Key UI & Settings Modal ──────────────────────────────────────────

export function openSettingsModal(): void {
  const modal = document.getElementById('settings-modal');
  if (!modal) return;
  modal.classList.remove('hidden');

  const st = byok.keyState();
  const input = document.getElementById('settings-key-input') as HTMLInputElement | null;
  const statusLine = document.getElementById('settings-key-status');
  const clearBtn = document.getElementById('btn-settings-key-clear');
  const hint = document.getElementById('settings-key-hint');

  if (hint) hint.classList.add('hidden');
  if (input) {
    input.value = '';
    input.placeholder = st.saved ? `Current key: ${st.masked || '••••••••'}` : 'AIza... or sk-or-v1-...';
  }

  if (statusLine) {
    if (st.saved) {
      const provName = st.provider === 'gemini' ? 'Google Gemini' : st.provider === 'openrouter' ? 'OpenRouter' : 'API';
      statusLine.innerHTML = `<span class="text-emerald-400 font-semibold">✓ ${provName} key active</span> (${st.masked})`;
    } else {
      statusLine.innerHTML = `<span class="text-amber-400 font-semibold">⚠ No API key configured</span>`;
    }
  }

  if (clearBtn) {
    clearBtn.classList.toggle('hidden', !st.saved);
  }
}

export function closeSettingsModal(): void {
  const modal = document.getElementById('settings-modal');
  if (!modal) return;
  modal.classList.add('hidden');
}

function refreshHeaderKeyBadge(): void {
  const badge = document.getElementById('header-key-badge');
  if (!badge) return;

  const st = byok.keyState();
  if (st.saved && st.validated) {
    const prov = st.provider === 'gemini' ? 'Gemini' : st.provider === 'openrouter' ? 'OpenRouter' : 'Key';
    badge.className = 'px-3 py-1.5 rounded-lg text-xs font-bold bg-slate-800 hover:bg-slate-700 text-emerald-400 border border-emerald-500/40 flex items-center gap-1.5 transition-colors cursor-pointer';
    badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-emerald-400"></span> ${prov} Key: ${st.masked || 'Active'}`;
  } else {
    badge.className = 'px-3 py-1.5 rounded-lg text-xs font-bold bg-amber-950/80 hover:bg-amber-900 text-amber-300 border border-amber-600/80 flex items-center gap-1.5 transition-colors cursor-pointer animate-pulse';
    badge.innerHTML = `<span class="w-2 h-2 rounded-full bg-amber-400"></span> 🔑 Set API Key (Required)`;
  }
}

function wireSettingsModal(): void {
  document.getElementById('header-key-badge')?.addEventListener('click', openSettingsModal);
  document.getElementById('btn-open-settings')?.addEventListener('click', openSettingsModal);
  document.getElementById('btn-close-settings')?.addEventListener('click', closeSettingsModal);

  const verifyBtn = document.getElementById('btn-settings-key-verify');
  const input = document.getElementById('settings-key-input') as HTMLInputElement | null;
  const hint = document.getElementById('settings-key-hint');
  const clearBtn = document.getElementById('btn-settings-key-clear');

  if (verifyBtn && input) {
    verifyBtn.addEventListener('click', async () => {
      const val = input.value.trim();
      if (!val) {
        if (hint) {
          hint.textContent = 'Please paste your Google Gemini or OpenRouter API key first.';
          hint.className = 'text-xs text-amber-400 font-semibold mt-2';
          hint.classList.remove('hidden');
        }
        return;
      }

      verifyBtn.textContent = 'Verifying...';
      (verifyBtn as HTMLButtonElement).disabled = true;

      const out = await byok.verify(val);
      (verifyBtn as HTMLButtonElement).disabled = false;
      verifyBtn.textContent = 'Validate & Save';

      if (hint) {
        hint.textContent = out.message;
        hint.className = `text-xs font-semibold mt-2 ${out.ok ? 'text-emerald-400' : 'text-rose-400'}`;
        hint.classList.remove('hidden');
      }

      if (out.ok) {
        input.value = '';
        input.placeholder = `Current key: ${out.masked || '••••••••'}`;
        refreshHeaderKeyBadge();
        setTimeout(() => {
          closeSettingsModal();
        }, 1200);
      }
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener('click', async () => {
      if (confirm('Remove saved API key from this browser?')) {
        await byok.clearKey();
        if (input) input.value = '';
        if (hint) hint.classList.add('hidden');
        refreshHeaderKeyBadge();
        openSettingsModal();
      }
    });
  }

  // Toggle password visibility
  const toggleBtn = document.getElementById('btn-toggle-key-visibility');
  if (toggleBtn && input) {
    toggleBtn.addEventListener('click', () => {
      if (input.type === 'password') {
        input.type = 'text';
        toggleBtn.textContent = 'Hide';
      } else {
        input.type = 'password';
        toggleBtn.textContent = 'Show';
      }
    });
  }
}

// ── Debate Run Hook ──────────────────────────────────────────────────

function onRun(idea: Idea): void {
  const key = byok.storedKey();
  if (!key) {
    openSettingsModal();
    const hint = document.getElementById('settings-key-hint');
    if (hint) {
      hint.textContent = 'Please enter and validate your Google Gemini API key to start this debate.';
      hint.className = 'text-xs text-amber-400 font-semibold mt-2';
      hint.classList.remove('hidden');
    }
    return;
  }

  // Scroll to debate section
  document.getElementById('debate-run')?.scrollIntoView({ behavior: 'smooth' });

  debate.start(idea, key, {
    urls: idea.urls,
    onFinish: (updatedIdea) => {
      void shell.refresh();
      // Show complete feedback
      const statusLine = document.getElementById('status-line');
      if (statusLine) {
        statusLine.textContent = `Debate finished for "${updatedIdea.title}". Verdict: ${updatedIdea.verdict || 'DONE'}`;
      }
    },
  });
}

// ── Boot ─────────────────────────────────────────────────────────────

async function boot(): Promise<void> {
  await byok.initByok();
  refreshHeaderKeyBadge();
  wireSettingsModal();
  shell.init({ onRun });
}

void boot();

// Export for test inspection and UI handlers
(window as unknown as Record<string, unknown>).__idealint = { shell, debate, byok, openSettingsModal, closeSettingsModal };
(window as unknown as Record<string, unknown>).__venturebot = { shell, debate, byok };