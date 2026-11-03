/* Settings — live health, sources, budget. */
(function () {
  'use strict';
  const JP = window.JP;
  const el = (id) => document.getElementById(id);

  function row(key, value, mono) {
    return `<div class="settings-row"><span class="settings-row__key">${JP.escapeHtml(key)}</span>
      <span class="settings-row__value ${mono ? 'settings-row__value--mono' : ''}">${JP.escapeHtml(value)}</span></div>`;
  }

  async function loadHealth() {
    const wrap = el('health-rows');
    if (!wrap) return;
    try {
      const data = await JP.api('/api/health');
      const db = data.database || {};
      const budget = data.budget || {};
      wrap.innerHTML =
        row('Server', data.ok ? 'healthy' : 'degraded') +
        row('Version', data.version || '0.1.0', true) +
        row('Agent mode', data.llm_enabled ? 'LLM gateway' : 'local-only') +
        row('Database', db.ok ? `connected · ${db.size_kb || 0} KB` : `error: ${db.error || 'unknown'}`) +
        row('Jobs indexed', String(db.jobs || 0)) +
        row('Applications', String(db.applications || 0)) +
        row('Active memories', String(db.memories || 0)) +
        row('Spent today', `$${Number(budget.spent_today || 0).toFixed(4)}`) +
        row('Remaining budget', `$${Number(budget.remaining || 0).toFixed(2)}`) +
        row('Checked', new Date(data.time).toLocaleTimeString());
    } catch (error) {
      wrap.innerHTML = row('Health check failed', error.message);
    }
  }

  function sourceRow(source) {
    const probe = source.last_probe || {};
    const status = source.status || (source.enabled ? 'unknown' : 'disabled');
    return `<div class="source-row">
      <span class="status-dot status-dot--${status === 'live' ? 'live' : status === 'down' ? 'down' : 'warn'}"></span>
      <div class="source-row__main">
        <strong>${JP.escapeHtml(source.name)}</strong>
        <small>priority ${source.priority}${source.requires_key ? ' · requires key' : ''}${probe.detail ? ` · ${JP.escapeHtml(String(probe.detail).slice(0, 70))}` : ''}</small>
      </div>
      <span class="source-row__latency">${probe.latency_ms ? `${Math.round(probe.latency_ms)}ms` : '—'}</span>
      <span class="badge badge--${status === 'live' ? 'success' : status === 'down' ? 'danger' : 'neutral'}">${JP.escapeHtml(status)}</span>
    </div>`;
  }

  async function loadSources(force) {
    const wrap = el('source-rows');
    if (!wrap) return;
    if (force) wrap.innerHTML = '<div class="settings-row"><span class="settings-row__key">Probing…</span><span class="settings-row__value">Contacting every source</span></div>';
    try {
      const data = await JP.api(force ? '/api/sources/check' : '/api/sources', force ? { method: 'POST' } : {});
      const sources = data.sources || [];
      wrap.innerHTML = sources.length
        ? sources.map(sourceRow).join('')
        : '<div class="settings-row"><span class="settings-row__key">Sources</span><span class="settings-row__value">none configured</span></div>';
      if (force) {
        const live = sources.filter((source) => source.status === 'live').length;
        JP.toast(`${live} of ${sources.length} sources responded.`, { tone: live ? 'success' : 'error', title: 'Probe complete' });
      }
    } catch (error) {
      wrap.innerHTML = `<div class="settings-row"><span class="settings-row__key">Sources</span><span class="settings-row__value">${JP.escapeHtml(error.message)}</span></div>`;
    }
  }

  function boot() {
    el('health-refresh')?.addEventListener('click', () => {
      loadHealth();
      loadSources(false);
      JP.toast('Health refreshed.', { tone: 'info', duration: 2000 });
    });
    el('sources-check')?.addEventListener('click', async () => {
      const button = el('sources-check');
      if (button) {
        button.disabled = true;
        button.classList.add('is-loading');
      }
      await loadSources(true);
      if (button) {
        button.disabled = false;
        button.classList.remove('is-loading');
      }
      loadHealth();
    });
    loadHealth();
    loadSources(false);
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
