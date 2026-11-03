/* Dashboard — top matches, pipeline snapshot, runs, sources. */
(function () {
  'use strict';
  const JP = window.JP;

  const jobRow = (job) => `
    <a class="list-row" href="/jobs?job=${encodeURIComponent(job.id)}">
      <span class="company-logo">${JP.escapeHtml(JP.initials(job.company))}</span>
      <span class="list-row__main">
        <span class="list-row__title">${JP.escapeHtml(job.title)}</span>
        <span class="list-row__meta">
          <span>${JP.escapeHtml(job.company || 'Unknown')}</span>
          ${job.location ? '<span class="dot"></span><span>' + JP.escapeHtml(job.location) + '</span>' : ''}
          ${job.remote ? '<span class="badge badge--cyan">Remote</span>' : ''}
          ${job.salary ? '<span class="dot"></span><span>' + JP.escapeHtml(job.salary) + '</span>' : ''}
        </span>
      </span>
      ${JP.scoreRing(job.score, 44)}
    </a>`;

  const runRow = (run) => `
    <a class="list-row" href="/runs?run=${encodeURIComponent(run.id)}">
      <span class="stat-card__icon stat-card__icon--${run.status === 'failed' ? 'amber' : 'cyan'}" style="width:30px;height:30px">
        ${JP.icon(run.status === 'failed' ? 'alert' : 'zap', 15)}
      </span>
      <span class="list-row__main">
        <span class="list-row__title">${JP.escapeHtml(run.goal || 'Untitled run')}</span>
        <span class="list-row__meta">
          <span class="badge badge--${JP.statusTone(run.status)}">${JP.escapeHtml(run.status)}</span>
          <span class="dot"></span><span>${JP.escapeHtml(run.mode || 'local')}</span>
          <span class="dot"></span><span>${JP.escapeHtml(JP.timeAgo(run.started_at))}</span>
        </span>
      </span>
      ${JP.icon('chevron-right', 15)}
    </a>`;

  function renderStats(data) {
    const jobs = data.jobs || {};
    const apps = data.applications || {};
    const memory = data.memory || {};
    const set = (id, value, meta) => {
      const el = document.getElementById(id);
      if (el) el.textContent = value;
      if (meta !== undefined) {
        const metaEl = document.getElementById(`${id}-meta`);
        if (metaEl) metaEl.textContent = meta;
      }
    };
    set('stat-jobs', jobs.total || 0, `${(jobs.sources || []).length} source${(jobs.sources || []).length === 1 ? '' : 's'} indexed`);
    set('stat-saved', jobs.saved || 0, jobs.remote ? `${jobs.remote} remote` : 'Bookmarked for review');
    set('stat-apps', apps.total || 0, apps.active ? `${apps.active} active · ${apps.response_rate || 0}% response` : 'Across every stage');
    set('stat-memory', memory.active || 0, memory.pending ? `${memory.pending} awaiting confirmation` : 'Preferences the agent knows');
  }

  function renderTopJobs(data) {
    const wrap = document.getElementById('top-jobs');
    if (!wrap) return;
    const jobs = data.top_jobs || [];
    const empty = data.has_activity
      ? JP.emptyState('search', 'No matches yet', 'Search live sources to populate ranked roles.', '<a class="btn btn--secondary btn--sm" href="/jobs">Search jobs</a>')
      : JP.emptyState(
          'sparkles',
          'Start with a search',
          'Ask JobPilot for roles and the agent will search, dedupe and rank them.',
          '<a class="btn btn--primary btn--sm" href="/agent">Ask the agent</a>'
        );
    wrap.innerHTML = jobs.length ? jobs.map(jobRow).join('') : empty;
  }

  function renderRuns(data) {
    const wrap = document.getElementById('recent-runs');
    if (!wrap) return;
    const runs = data.recent_runs || [];
    wrap.innerHTML = runs.length
      ? runs.map(runRow).join('')
      : '<div class="list-empty">No runs recorded yet. Ask the agent something to create a trace.</div>';
  }

  function renderPipeline(data) {
    const wrap = document.getElementById('pipeline');
    if (!wrap) return;
    const apps = data.applications || {};
    const by = apps.by_status || {};
    const total = apps.total || 0;
    const order = [
      ['saved', 'Saved', ''],
      ['applied', 'Applied', ''],
      ['screening', 'Screening', 'progress--amber'],
      ['interview', 'Interview', 'progress--amber'],
      ['offer', 'Offer', 'progress--green'],
      ['rejected', 'Rejected', 'progress--amber'],
    ];
    if (!total) {
      wrap.innerHTML = '<div class="list-empty">Nothing in the pipeline yet. Track a job to start.</div>';
      return;
    }
    wrap.innerHTML =
      order
        .map(([key, label, tone]) => {
          const count = by[key] || 0;
          const pct = total ? Math.round((count / total) * 100) : 0;
          return `<div class="pipeline-row">
            <div class="pipeline-row__top"><span>${label}</span><span>${count}</span></div>
            <div class="progress ${tone}"><span style="width:${Math.max(count ? 4 : 0, pct)}%"></span></div>
          </div>`;
        })
        .join('') +
      `<div class="pipeline-row" style="border-top:1px solid var(--border);margin-top:6px">
        <div class="pipeline-row__top"><span>Response rate</span><span>${apps.response_rate || 0}%</span></div>
      </div>`;
  }

  function renderFollowups(data) {
    const wrap = document.getElementById('followups');
    if (!wrap) return;
    const items = data.followups || [];
    if (!items.length) {
      wrap.innerHTML = '<div class="list-empty">No follow-ups scheduled. Add an application to start the clock.</div>';
      return;
    }
    wrap.innerHTML = items
      .map(
        (item) => `
        <a class="list-row" href="/tracker">
          <span class="company-logo">${JP.escapeHtml(JP.initials(item.job ? item.job.company : '?'))}</span>
          <span class="list-row__main">
            <span class="list-row__title">${JP.escapeHtml(item.job ? item.job.title : 'Application')}</span>
            <span class="list-row__meta">
              <span class="badge badge--${JP.statusTone(item.status)}">${JP.escapeHtml(item.status)}</span>
              <span class="dot"></span><span>Follow up ${JP.escapeHtml(JP.formatDate(item.follow_up_at))}</span>
            </span>
          </span>
        </a>`
      )
      .join('');
  }

  function renderProfile(data) {
    const completeness = Number(data.profile_completeness || 0);
    const bar = document.getElementById('profile-bar');
    const text = document.getElementById('profile-nudge-text');
    const link = document.getElementById('profile-nudge-link');
    if (bar) bar.style.width = `${completeness}%`;
    if (text) {
      text.textContent =
        completeness >= 90
          ? 'Your profile is in great shape. Tailoring will use every detail.'
          : `You are ${completeness}% complete. Add measurable bullets and target roles to improve matching.`;
    }
    if (link) link.textContent = completeness >= 90 ? 'Review profile' : 'Complete profile';
  }

  async function loadSources() {
    const wrap = document.getElementById('source-strip');
    if (!wrap) return;
    try {
      const data = await JP.api('/api/sources');
      const sources = (data.sources || []).slice(0, 4);
      wrap.innerHTML = sources.length
        ? sources
            .map(
              (source) => `
          <a class="source-pill" href="/settings#sources" title="${JP.escapeAttr(source.name)}">
            <span class="status-dot status-dot--${source.status === 'live' ? 'live' : source.status === 'down' ? 'down' : 'warn'}"></span>
            <strong>${JP.escapeHtml(source.name)}</strong>
            <span>${source.last_probe && source.last_probe.latency_ms ? Math.round(source.last_probe.latency_ms) + 'ms' : 'not probed'}</span>
          </a>`
            )
            .join('')
        : '<div class="list-empty">No sources configured.</div>';
    } catch (error) {
      wrap.innerHTML = `<div class="list-empty">${JP.escapeHtml(error.message)}</div>`;
    }
  }

  function initPrompt() {
    const form = document.getElementById('hero-prompt');
    const input = document.getElementById('hero-input');
    if (!form || !input) return;
    const launch = (prompt) => {
      if (!prompt) return;
      try {
        sessionStorage.setItem('jobpilot:agent-prompt', prompt);
      } catch (error) {
        /* ignore */
      }
      window.location.href = '/agent';
    };
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      launch(input.value.trim());
    });
    JP.$$('.suggestion[data-prompt]').forEach((button) => {
      button.addEventListener('click', () => launch(button.dataset.prompt));
    });
  }

  async function boot() {
    initPrompt();
    loadSources();
    try {
      const data = await JP.api('/api/dashboard');
      renderStats(data);
      renderTopJobs(data);
      renderRuns(data);
      renderPipeline(data);
      renderFollowups(data);
      renderProfile(data);
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
      const jobs = document.getElementById('top-jobs');
      if (jobs) jobs.innerHTML = `<div class="list-empty">${JP.escapeHtml(error.message)}</div>`;
    }
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
