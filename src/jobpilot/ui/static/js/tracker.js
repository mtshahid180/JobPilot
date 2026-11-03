/* Tracker — kanban with drag/drop, table view, application timeline. */
(function () {
  'use strict';
  const JP = window.JP;

  const STAGES = [
    { key: 'saved', label: 'Saved' },
    { key: 'applied', label: 'Applied' },
    { key: 'screening', label: 'Screening' },
    { key: 'interview', label: 'Interview' },
    { key: 'offer', label: 'Offer' },
    { key: 'rejected', label: 'Rejected' },
    { key: 'withdrawn', label: 'Withdrawn' },
  ];

  const state = {
    applications: [],
    stats: {},
    view: 'board',
    filter: '',
    dragging: null,
  };

  const el = (id) => document.getElementById(id);

  // --- render -------------------------------------------------------------
  function jobOf(app) {
    return app.job || { title: 'Untitled role', company: 'Unknown', score: null };
  }

  function card(app) {
    const job = jobOf(app);
    return `<div class="kanban-card" draggable="true" data-app="${JP.escapeAttr(app.id)}" data-status="${JP.escapeAttr(app.status)}">
      <div class="kanban-card__title">${JP.escapeHtml(job.title)}</div>
      <div class="kanban-card__company">${JP.escapeHtml(job.company || 'Unknown')}</div>
      <div class="kanban-card__meta">
        <span class="badge badge--${JP.statusTone(app.status)}">${JP.escapeHtml(app.status)}</span>
        ${job.score !== null && job.score !== undefined ? `<span class="badge badge--neutral">${Math.round(job.score)} match</span>` : ''}
        ${job.remote ? '<span class="badge badge--cyan">Remote</span>' : ''}
      </div>
      <div class="kanban-card__foot">
        <span>${JP.escapeHtml(JP.timeAgo(app.updated_at))}</span>
        <span>${app.follow_up_at ? `follow-up ${JP.escapeHtml(JP.formatDate(app.follow_up_at))}` : `${(app.history || []).length} event${(app.history || []).length === 1 ? '' : 's'}`}</span>
      </div>
    </div>`;
  }

  function renderBoard() {
    const board = el('kanban');
    if (!board) return;
    const stages = state.filter ? STAGES.filter((stage) => stage.key === state.filter) : STAGES;
    board.innerHTML = stages
      .map((stage) => {
        const items = state.applications.filter((app) => app.status === stage.key);
        return `<section class="kanban-column kanban-column--${stage.key}" data-drop="${stage.key}">
          <div class="kanban-column__head">
            <span class="kanban-column__dot"></span>
            <h3>${stage.label}</h3>
            <span class="kanban-column__count">${items.length}</span>
          </div>
          <div class="kanban-column__body">
            ${items.length ? items.map(card).join('') : '<div class="kanban-empty">Drop a card here</div>'}
          </div>
        </section>`;
      })
      .join('');

    JP.$$('.kanban-card', board).forEach((node) => {
      node.addEventListener('click', () => openApplication(node.dataset.app));
      node.addEventListener('dragstart', (event) => {
        state.dragging = node.dataset.app;
        node.classList.add('is-dragging');
        try {
          event.dataTransfer.setData('text/plain', node.dataset.app);
          event.dataTransfer.effectAllowed = 'move';
        } catch (error) {
          /* older browsers */
        }
      });
      node.addEventListener('dragend', () => {
        node.classList.remove('is-dragging');
        state.dragging = null;
        JP.$$('.kanban-column', board).forEach((column) => column.classList.remove('is-over'));
      });
    });

    JP.$$('[data-drop]', board).forEach((column) => {
      column.addEventListener('dragover', (event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
        column.classList.add('is-over');
      });
      column.addEventListener('dragleave', (event) => {
        if (!column.contains(event.relatedTarget)) column.classList.remove('is-over');
      });
      column.addEventListener('drop', (event) => {
        event.preventDefault();
        column.classList.remove('is-over');
        const id = state.dragging || event.dataTransfer.getData('text/plain');
        if (id && column.dataset.drop) move(id, column.dataset.drop);
      });
    });
  }

  function renderTable() {
    const tbody = el('applications-table');
    if (!tbody) return;
    const items = state.filter ? state.applications.filter((app) => app.status === state.filter) : state.applications;
    if (!items.length) {
      tbody.innerHTML = `<tr><td colspan="6"><div class="list-empty">No applications${state.filter ? ' in this stage' : ''} yet.</div></td></tr>`;
      return;
    }
    tbody.innerHTML = items
      .map((app) => {
        const job = jobOf(app);
        return `<tr data-app="${JP.escapeAttr(app.id)}">
          <td><strong>${JP.escapeHtml(job.title)}</strong></td>
          <td>${JP.escapeHtml(job.company || 'Unknown')}</td>
          <td><span class="badge badge--${JP.statusTone(app.status)}">${JP.escapeHtml(app.status)}</span></td>
          <td class="mono">${JP.escapeHtml(JP.timeAgo(app.updated_at))}</td>
          <td class="mono">${JP.escapeHtml(app.follow_up_at ? JP.formatDate(app.follow_up_at) : '—')}</td>
          <td class="right">${JP.icon('chevron-right', 14)}</td>
        </tr>`;
      })
      .join('');
    JP.$$('tr[data-app]', tbody).forEach((row) => {
      row.addEventListener('click', () => openApplication(row.dataset.app));
    });
  }

  function renderStats(stats) {
    const set = (id, value, meta) => {
      const node = el(id);
      if (node) node.textContent = value;
      if (meta !== undefined) {
        const metaNode = el(`${id}-meta`);
        if (metaNode) metaNode.textContent = meta;
      }
    };
    set('app-stat-total', stats.total || 0, stats.total ? 'Every application' : 'Nothing tracked yet');
    set('app-stat-active', stats.active || 0, 'Applied through offer');
    set('app-stat-interviews', stats.interviews || 0, stats.offers ? `${stats.offers} offer${stats.offers === 1 ? '' : 's'} received` : 'Conversations booked');
    set('app-stat-rate', `${stats.response_rate || 0}%`, 'Screening or further');
  }

  function renderAll() {
    renderStats(state.stats || {});
    renderBoard();
    renderTable();
    const updated = el('tracker-updated');
    if (updated) updated.textContent = `Updated ${new Date().toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`;
  }

  // --- data ---------------------------------------------------------------
  async function load(showToast) {
    try {
      const data = await JP.api(`/api/applications${state.filter ? `?status=${encodeURIComponent(state.filter)}` : ''}`);
      const all = data.applications || [];
      // Keep all applications in memory so board columns stay complete; API filter is applied client-side.
      state.applications = all;
      state.stats = data.stats || {};
      if (!state.filter) renderAll();
      else renderAll();
      if (showToast) JP.toast('Tracker refreshed.', { tone: 'info', duration: 2000 });
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
    }
  }

  function find(appId) {
    return state.applications.find((app) => app.id === appId);
  }

  async function move(appId, status) {
    const app = find(appId);
    if (!app || app.status === status) return;
    const previous = app.status;
    app.status = status;
    renderAll();
    try {
      const data = await JP.api(`/api/applications/${encodeURIComponent(appId)}`, {
        method: 'PATCH',
        body: { status, note: `moved from ${previous} to ${status} in tracker` },
      });
      Object.assign(app, data.application || {});
      await load();
      JP.toast(`Moved to ${status}.`, { tone: 'info', duration: 2400 });
    } catch (error) {
      app.status = previous;
      renderAll();
      JP.toast(error.message, { tone: 'error' });
    }
  }

  // --- detail modal -------------------------------------------------------
  function timeline(app) {
    const history = (app.history || []).slice().reverse();
    if (!history.length) return '<p class="tiny faint">No status events yet.</p>';
    return `<div class="application-timeline">${history
      .map(
        (event) => `<div class="timeline-item">
          <span class="timeline-item__dot"></span>
          <div class="timeline-item__main">
            <strong>${JP.escapeHtml(event.status)}</strong>
            <small>${JP.escapeHtml(JP.formatDate(event.at))} · ${JP.escapeHtml(JP.timeAgo(event.at))}</small>
            ${event.note ? `<div class="tiny muted">${JP.escapeHtml(event.note)}</div>` : ''}
          </div>
        </div>`
      )
      .join('')}</div>`;
  }

  function openApplication(appId) {
    const app = find(appId);
    if (!app) return;
    const job = jobOf(app);
    JP.modal({
      title: job.title,
      subtitle: `${job.company || 'Unknown'}${job.location ? ` · ${job.location}` : ''} · tracked ${JP.timeAgo(app.created_at)}`,
      wide: true,
      body: `
        <div class="row-12 wrap">
          ${JP.scoreRing(job.score, 52)}
          <div class="chip-list">
            <span class="badge badge--${JP.statusTone(app.status)}">${JP.escapeHtml(app.status)}</span>
            ${job.remote ? '<span class="badge badge--cyan">Remote</span>' : ''}
            ${job.source ? `<span class="badge badge--neutral">${JP.escapeHtml(job.source)}</span>` : ''}
          </div>
        </div>
        <div class="form-grid">
          <div class="field">
            <label for="app-status">Status</label>
            <select class="select" id="app-status">
              ${STAGES.map((stage) => `<option value="${stage.key}" ${app.status === stage.key ? 'selected' : ''}>${stage.label}</option>`).join('')}
            </select>
          </div>
          <div class="field">
            <label for="app-note">Note <span class="faint">(optional)</span></label>
            <input class="input" id="app-note" type="text" placeholder="Recruiter call booked for Tuesday">
          </div>
        </div>
        <div class="field">
          <label for="app-notes">Notes</label>
          <textarea class="textarea" id="app-notes" placeholder="Context worth remembering: recruiter name, salary range, next step…">${JP.escapeHtml(app.notes || '')}</textarea>
        </div>
        <div>
          <div class="section-label">History</div>
          <div class="mt-8">${timeline(app)}</div>
        </div>
        <div class="field">
          <label>Links</label>
          <div class="row-8 wrap">
            ${job.id ? `<a class="btn btn--secondary btn--sm" href="/jobs?job=${encodeURIComponent(job.id)}">${JP.icon('briefcase', 14)} Open role</a>` : ''}
            ${job.url ? `<a class="btn btn--ghost btn--sm" href="${JP.escapeAttr(job.url)}" target="_blank" rel="noopener noreferrer">${JP.icon('external', 14)} Original posting</a>` : ''}
            <a class="btn btn--ghost btn--sm" href="/drafts">${JP.icon('file-text', 14)} Drafts</a>
          </div>
        </div>`,
      footer: `<button class="btn btn--danger btn--sm" type="button" id="app-delete" style="margin-right:auto">${JP.icon('trash', 14)} Delete</button>
        <button class="btn btn--ghost" type="button" data-modal-close>Cancel</button>
        <button class="btn btn--primary" type="button" id="app-save">${JP.icon('check', 14)} Save changes</button>`,
      onMount(root) {
        root.querySelector('#app-save').addEventListener('click', async () => {
          const button = root.querySelector('#app-save');
          button.classList.add('is-loading');
          try {
            const data = await JP.api(`/api/applications/${encodeURIComponent(app.id)}`, {
              method: 'PATCH',
              body: {
                status: root.querySelector('#app-status').value,
                note: root.querySelector('#app-note').value.trim() || 'updated in tracker',
              },
            });
            const notes = root.querySelector('#app-notes').value.trim();
            if (notes !== (app.notes || '')) {
              await JP.api(`/api/applications/${encodeURIComponent(app.id)}`, {
                method: 'PATCH',
                body: { status: data.application.status, note: `notes: ${notes.slice(0, 160)}` },
              });
            }
            JP.closeModal();
            JP.toast('Application updated.', { title: 'Saved' });
            await load();
          } catch (error) {
            JP.toast(error.message, { tone: 'error' });
            button.classList.remove('is-loading');
          }
        });
        root.querySelector('#app-delete').addEventListener('click', async () => {
          const confirmed = await JP.confirm({
            title: 'Delete this application?',
            message: 'The job stays in your library, but the application history will be removed.',
            confirmLabel: 'Delete',
            danger: true,
          });
          if (!confirmed) return;
          try {
            await JP.api(`/api/applications/${encodeURIComponent(app.id)}`, { method: 'DELETE' });
            JP.closeModal();
            JP.toast('Application deleted.', { tone: 'info' });
            await load();
          } catch (error) {
            JP.toast(error.message, { tone: 'error' });
          }
        });
      },
    });
  }

  // --- add ----------------------------------------------------------------
  async function addApplication() {
    let jobs = [];
    try {
      const data = await JP.api('/api/jobs?sort=score&limit=100');
      jobs = data.jobs || [];
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
      return;
    }
    if (!jobs.length) {
      JP.modal({
        title: 'No jobs to track yet',
        subtitle: 'Search or import a role first.',
        body: JP.emptyState('briefcase', 'Your library is empty', 'Search live sources or import a posting, then come back to track it.', '<a class="btn btn--primary btn--sm" href="/jobs">Open job explorer</a>'),
        footer: '<button class="btn btn--ghost" type="button" data-modal-close>Close</button>',
      });
      return;
    }
    JP.modal({
      title: 'Track an application',
      subtitle: 'Pick a role from your library. You can change the stage later.',
      body: `<div class="field">
          <label for="add-job">Role</label>
          <select class="select" id="add-job">
            ${jobs.map((job) => `<option value="${JP.escapeAttr(job.id)}">${JP.escapeHtml(job.title)} — ${JP.escapeHtml(job.company || 'Unknown')}${job.score ? ` (${Math.round(job.score)})` : ''}</option>`).join('')}
          </select>
        </div>
        <div class="form-grid">
          <div class="field">
            <label for="add-status">Stage</label>
            <select class="select" id="add-status">
              <option value="applied">Applied</option>
              <option value="saved">Saved</option>
              <option value="screening">Screening</option>
              <option value="interview">Interview</option>
              <option value="offer">Offer</option>
            </select>
          </div>
          <div class="field">
            <label for="add-notes">Notes</label>
            <input class="input" id="add-notes" type="text" placeholder="Referred by…">
          </div>
        </div>`,
      footer: `<button class="btn btn--ghost" type="button" data-modal-close>Cancel</button>
        <button class="btn btn--primary" type="button" id="add-submit">${JP.icon('plus', 14)} Track application</button>`,
      onMount(root) {
        root.querySelector('#add-submit').addEventListener('click', async () => {
          const button = root.querySelector('#add-submit');
          button.classList.add('is-loading');
          try {
            await JP.api('/api/applications', {
              method: 'POST',
              body: {
                job_id: root.querySelector('#add-job').value,
                status: root.querySelector('#add-status').value,
                notes: root.querySelector('#add-notes').value.trim(),
              },
            });
            JP.closeModal();
            JP.toast('Application tracked.', { title: 'Added to pipeline' });
            await load();
          } catch (error) {
            JP.toast(error.message, { tone: 'error' });
            button.classList.remove('is-loading');
          }
        });
      },
    });
  }

  // --- boot ---------------------------------------------------------------
  function boot() {
    el('tracker-refresh')?.addEventListener('click', () => load(true));
    el('add-application')?.addEventListener('click', addApplication);
    el('tracker-filter')?.addEventListener('change', (event) => {
      state.filter = event.target.value;
      renderAll();
    });
    JP.$$('[data-view]').forEach((tab) => {
      tab.addEventListener('click', () => {
        state.view = tab.dataset.view;
        JP.$$('[data-view]').forEach((other) => {
          const active = other === tab;
          other.classList.toggle('is-active', active);
          other.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        el('kanban')?.classList.toggle('hidden', state.view !== 'board');
        el('table-view')?.classList.toggle('hidden', state.view !== 'table');
      });
    });
    load();
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
