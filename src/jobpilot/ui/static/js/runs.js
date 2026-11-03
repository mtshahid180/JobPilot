/* Runs & traces — run log and per-run trace inspector. */
(function () {
  'use strict';
  const JP = window.JP;

  const state = { runs: [], selected: '' };
  const el = (id) => document.getElementById(id);

  function renderStats(stats) {
    const set = (id, value, meta) => {
      const node = el(id);
      if (node) node.textContent = value;
      if (meta !== undefined) {
        const metaNode = el(`${id}-meta`);
        if (metaNode) metaNode.textContent = meta;
      }
    };
    set('run-stat-total', stats.total_runs || 0, stats.total_runs ? 'All agent executions' : 'No runs yet');
    set('run-stat-completed', stats.completed_runs || 0, stats.total_runs ? `${Math.round(((stats.completed_runs || 0) / stats.total_runs) * 100)}% completion` : 'Finished without error');
    set('run-stat-tokens', (stats.total_tokens || 0).toLocaleString(), 'Only in LLM mode');
    set('run-stat-cost', `$${Number(stats.total_cost || 0).toFixed(4)}`, 'Budget tracked per call');
  }

  function renderTable() {
    const tbody = el('runs-table');
    const count = el('run-count');
    if (count) count.textContent = `${state.runs.length} run${state.runs.length === 1 ? '' : 's'}`;
    if (!tbody) return;
    if (!state.runs.length) {
      tbody.innerHTML = '<tr><td colspan="6"><div class="list-empty">No runs recorded yet. Ask the agent to do something.</div></td></tr>';
      return;
    }
    tbody.innerHTML = state.runs
      .map(
        (run) => `<tr data-run="${JP.escapeAttr(run.id)}" class="${state.selected === run.id ? 'is-selected' : ''}">
          <td><strong>${JP.escapeHtml(run.goal || 'Untitled run')}</strong><div class="mono">${JP.escapeHtml((run.id || '').slice(0, 14))}</div></td>
          <td><span class="badge badge--${JP.statusTone(run.status)}">${JP.escapeHtml(run.status)}</span></td>
          <td class="mono">${JP.escapeHtml(run.mode || 'local')}</td>
          <td class="mono">${JP.escapeHtml(JP.timeAgo(run.started_at))}</td>
          <td>${run.steps || 0}</td>
          <td class="mono">$${Number(run.cost || 0).toFixed(4)}</td>
        </tr>`
      )
      .join('');
    JP.$$('tr[data-run]', tbody).forEach((row) => {
      row.addEventListener('click', () => openRun(row.dataset.run));
    });
  }

  function formatData(data) {
    if (data === null || data === undefined) return '';
    if (typeof data === 'string') return data;
    try {
      return JSON.stringify(data, null, 2);
    } catch (error) {
      return String(data);
    }
  }

  function eventMarkup(event) {
    const type = event.type || 'event';
    const tone =
      type === 'tool_call'
        ? 'cyan'
        : type === 'tool_result'
        ? 'green'
        : type === 'approval'
        ? 'amber'
        : type === 'error' || type === 'llm_error'
        ? 'danger'
        : 'accent';
    return `<div class="trace-event">
      <span class="trace-event__seq">${String(event.seq || '').padStart(2, '0')}</span>
      <div class="trace-event__main">
        <span class="trace-event__type" style="color:var(--${tone})">${JP.escapeHtml(type)}</span>
        <div class="trace-event__data">${JP.escapeHtml(formatData(event.data))}</div>
      </div>
      <span class="trace-event__time">${JP.escapeHtml(event.at ? new Date(event.at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '')}</span>
    </div>`;
  }

  async function openRun(runId) {
    state.selected = runId;
    renderTable();
    const body = el('trace-body');
    const badge = el('detail-status');
    if (body) body.innerHTML = '<div class="skeleton skeleton--title"></div><div class="skeleton skeleton--line"></div><div class="skeleton skeleton--line"></div>';
    try {
      const data = await JP.api(`/api/runs/${encodeURIComponent(runId)}`);
      const run = data.run || {};
      const trace = data.trace || [];
      if (badge) {
        badge.textContent = run.status || '—';
        badge.className = `badge badge--${JP.statusTone(run.status)}`;
      }
      if (!body) return;
      body.innerHTML = `
        <div class="run-meta-grid mb-16">
          <div class="run-meta"><span>Mode</span><strong>${JP.escapeHtml(run.mode || 'local')}</strong></div>
          <div class="run-meta"><span>Steps</span><strong>${run.steps || 0}</strong></div>
          <div class="run-meta"><span>Tokens</span><strong>${run.tokens || 0}</strong></div>
          <div class="run-meta"><span>Cost</span><strong>$${Number(run.cost || 0).toFixed(4)}</strong></div>
        </div>
        <div class="drawer__section mb-16">
          <h3>Goal</h3>
          <p class="small muted" style="line-height:1.6">${JP.escapeHtml(run.goal || '')}</p>
        </div>
        ${
          run.summary
            ? `<div class="drawer__section mb-16"><h3>Summary</h3><p class="small muted" style="line-height:1.6">${JP.escapeHtml(run.summary)}</p></div>`
            : ''
        }
        ${
          run.error
            ? `<div class="approval-card mb-16"><div class="approval-card__head">${JP.icon('alert', 14)} Error</div><p>${JP.escapeHtml(run.error)}</p></div>`
            : ''
        }
        <div class="inspector-section__head"><span>Trace · ${trace.length} events</span><span class="mono">${JP.escapeHtml((run.id || '').slice(0, 14))}</span></div>
        <div class="trace-list mt-8">${trace.length ? trace.map(eventMarkup).join('') : '<p class="tiny faint">No trace events stored.</p>'}</div>`;
    } catch (error) {
      if (body) body.innerHTML = `<div class="inspector__empty">${JP.escapeHtml(error.message)}</div>`;
    }
  }

  async function load() {
    try {
      const data = await JP.api('/api/runs');
      state.runs = data.runs || [];
      renderStats(data.stats || {});
      renderTable();
      const params = new URLSearchParams(window.location.search);
      const runId = params.get('run') || (state.runs[0] && state.runs[0].id);
      if (runId) openRun(runId);
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
      const tbody = el('runs-table');
      if (tbody) tbody.innerHTML = `<tr><td colspan="6"><div class="list-empty">${JP.escapeHtml(error.message)}</div></td></tr>`;
    }
  }

  function boot() {
    el('runs-refresh')?.addEventListener('click', () => load().then(() => JP.toast('Runs refreshed.', { tone: 'info', duration: 2000 })));
    load();
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
