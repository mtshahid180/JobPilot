/* Memory — persistent preferences, confirmation flow, search. */
(function () {
  'use strict';
  const JP = window.JP;

  const state = {
    active: [],
    pending: [],
    stats: {},
    kind: '',
    search: '',
  };

  const el = (id) => document.getElementById(id);

  function all() {
    return [...state.pending, ...state.active];
  }

  function filtered() {
    let items = state.kind === 'pending' ? state.pending.slice() : all();
    if (state.kind && state.kind !== 'pending') items = items.filter((memory) => memory.kind === state.kind);
    if (state.search) {
      const needle = state.search.toLowerCase();
      items = items.filter((memory) =>
        `${memory.content} ${(memory.tags || []).join(' ')} ${memory.provenance}`.toLowerCase().includes(needle)
      );
    }
    return items.sort((a, b) => {
      if (a.status === 'pending' && b.status !== 'pending') return -1;
      if (b.status === 'pending' && a.status !== 'pending') return 1;
      return new Date(b.created_at) - new Date(a.created_at);
    });
  }

  function memoryCard(memory) {
    const pending = memory.status === 'pending';
    const confidence = Math.round((Number(memory.confidence) || 0) * 100);
    return `<article class="memory-card ${pending ? 'memory-card--pending' : ''}" data-memory="${JP.escapeAttr(memory.id)}">
      <div class="memory-card__head">
        <span class="badge badge--${memory.kind === 'preference' ? 'cyan' : memory.kind === 'rule' ? 'warning' : memory.kind === 'episodic' ? 'accent' : 'neutral'}">${JP.escapeHtml(memory.kind)}</span>
        ${pending ? '<span class="badge badge--warning">Pending confirmation</span>' : memory.status !== 'active' ? `<span class="badge badge--neutral">${JP.escapeHtml(memory.status)}</span>` : ''}
        <span class="spacer"></span>
        <span class="tiny faint">${JP.escapeHtml(JP.timeAgo(memory.created_at))}</span>
      </div>
      <p class="memory-card__content">${JP.escapeHtml(memory.content)}</p>
      <div class="memory-card__meta">
        <span>${JP.icon('database', 11)} ${JP.escapeHtml(memory.provenance || 'agent')}</span>
        ${(memory.tags || []).slice(0, 3).map((tag) => `<span class="chip chip--muted">${JP.escapeHtml(tag)}</span>`).join('')}
        ${memory.use_count ? `<span>used ${memory.use_count}×</span>` : ''}
      </div>
      <div class="memory-card__bar">
        <div class="progress"><span style="width:${JP.clamp(confidence, 0, 100)}%"></span></div>
        <span>${confidence}% confidence</span>
      </div>
      <div class="memory-card__actions">
        ${
          pending
            ? `<button class="btn btn--success btn--xs" type="button" data-approve="${JP.escapeAttr(memory.id)}">${JP.icon('check', 12)} Confirm</button>
               <button class="btn btn--danger btn--xs" type="button" data-forget="${JP.escapeAttr(memory.id)}">${JP.icon('x', 12)} Reject</button>`
            : `<button class="btn btn--ghost btn--xs" type="button" data-copy="${JP.escapeAttr(memory.id)}">${JP.icon('copy', 12)} Copy</button>
               <button class="btn btn--ghost btn--xs" type="button" data-forget="${JP.escapeAttr(memory.id)}">${JP.icon('trash', 12)} Forget</button>`
        }
      </div>
    </article>`;
  }

  function render() {
    const grid = el('memory-grid');
    if (!grid) return;
    const items = filtered();
    if (!items.length) {
      grid.innerHTML = JP.emptyState(
        'brain',
        state.search ? 'No memories match that search' : 'Nothing remembered yet',
        state.search
          ? 'Try a different phrase, or clear the search to see everything.'
          : 'As you talk to the agent, confirmed preferences and rules appear here. You can also add one manually.',
        '<button class="btn btn--secondary btn--sm" type="button" id="empty-add-memory">Add memory</button>'
      );
      el('empty-add-memory')?.addEventListener('click', addMemory);
      return;
    }
    grid.innerHTML = items.map(memoryCard).join('');
    JP.$$('[data-approve]', grid).forEach((button) => {
      button.addEventListener('click', () => approve(button.dataset.approve));
    });
    JP.$$('[data-forget]', grid).forEach((button) => {
      button.addEventListener('click', () => forget(button.dataset.forget));
    });
    JP.$$('[data-copy]', grid).forEach((button) => {
      button.addEventListener('click', () => {
        const memory = all().find((item) => item.id === button.dataset.copy);
        if (memory) JP.copyText(memory.content);
      });
    });
  }

  function renderStats() {
    const stats = state.stats || {};
    const set = (id, value, meta) => {
      const node = el(id);
      if (node) node.textContent = value;
      if (meta !== undefined) {
        const metaNode = el(`${id}-meta`);
        if (metaNode) metaNode.textContent = meta;
      }
    };
    set('mem-stat-active', stats.active || 0, stats.active ? 'Used during every run' : 'Nothing active yet');
    set('mem-stat-pending', stats.pending || 0, stats.pending ? 'Waiting for confirmation' : 'No confirmations waiting');
    set('mem-stat-kinds', Object.keys(stats.by_kind || {}).length || 0, Object.entries(stats.by_kind || {}).map(([kind, count]) => `${count} ${kind}`).join(' · ') || 'Semantic, preference, rule');
    const pendingBadge = document.querySelector('.sidebar-nav a[href="/memory"]');
    if (pendingBadge && stats.pending) pendingBadge.dataset.pending = String(stats.pending);
  }

  async function load() {
    try {
      const data = await JP.api('/api/memories');
      state.active = data.memories || [];
      state.pending = data.pending || [];
      state.stats = data.stats || {};
      renderStats();
      render();
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
    }
  }

  async function approve(memoryId) {
    try {
      await JP.api(`/api/memories/${encodeURIComponent(memoryId)}/approve`, { method: 'POST' });
      JP.toast('Memory confirmed and added to active context.', { title: 'Confirmed' });
      await load();
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
    }
  }

  async function forget(memoryId) {
    const memory = all().find((item) => item.id === memoryId);
    const confirmed = await JP.confirm({
      title: 'Forget this memory?',
      message: memory ? `“${memory.content}” will no longer be used by the agent.` : 'This memory will no longer be used.',
      confirmLabel: 'Forget',
      danger: true,
    });
    if (!confirmed) return;
    try {
      await JP.api(`/api/memories/${encodeURIComponent(memoryId)}`, { method: 'DELETE' });
      JP.toast('Memory forgotten.', { tone: 'info' });
      await load();
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
    }
  }

  function addMemory() {
    JP.modal({
      title: 'Teach JobPilot something',
      subtitle: 'Preferences are stored immediately. Profile facts need confirmation.',
      body: `<div class="field">
          <label for="memory-content">Memory</label>
          <textarea class="textarea" id="memory-content" placeholder="I only want remote roles with a salary above $45k, and I prefer product companies over agencies."></textarea>
          <span class="field-hint">Write it as a durable instruction, not a one-off request.</span>
        </div>
        <div class="field">
          <label for="memory-kind">Kind</label>
          <select class="select" id="memory-kind">
            <option value="">Auto-detect</option>
            <option value="preference">Preference</option>
            <option value="rule">Rule</option>
            <option value="semantic">Semantic fact</option>
            <option value="episodic">Episodic event</option>
          </select>
        </div>`,
      footer: `<button class="btn btn--ghost" type="button" data-modal-close>Cancel</button>
        <button class="btn btn--primary" type="button" id="memory-submit">${JP.icon('brain', 14)} Save memory</button>`,
      onMount(root) {
        root.querySelector('#memory-content').focus();
        root.querySelector('#memory-submit').addEventListener('click', async () => {
          const content = root.querySelector('#memory-content').value.trim();
          if (!content) {
            JP.toast('Write a memory first.', { tone: 'error' });
            return;
          }
          const button = root.querySelector('#memory-submit');
          button.classList.add('is-loading');
          try {
            const data = await JP.api('/api/memories', {
              method: 'POST',
              body: { content, kind: root.querySelector('#memory-kind').value || null },
            });
            JP.closeModal();
            if (data.action === 'pending_confirmation') {
              JP.toast('This looks like a profile fact — confirm it below.', { tone: 'info', title: 'Confirmation needed' });
            } else {
              JP.toast(data.message || 'Memory saved.', { title: 'Remembered' });
            }
            await load();
          } catch (error) {
            JP.toast(error.message, { tone: 'error' });
            button.classList.remove('is-loading');
          }
        });
      },
    });
  }

  function boot() {
    el('add-memory')?.addEventListener('click', addMemory);
    el('memory-refresh')?.addEventListener('click', () => load().then(() => JP.toast('Memories refreshed.', { tone: 'info', duration: 2000 })));
    el('memory-search')?.addEventListener(
      'input',
      JP.debounce((event) => {
        state.search = event.target.value.trim();
        render();
      }, 180)
    );
    JP.$$('[data-kind]').forEach((tab) => {
      tab.addEventListener('click', () => {
        state.kind = tab.dataset.kind;
        JP.$$('[data-kind]').forEach((other) => other.classList.toggle('is-active', other === tab));
        render();
      });
    });
    load();
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
