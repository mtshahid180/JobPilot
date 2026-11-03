/* Job explorer — unified live search, filters, pagination, detail drawer. */
(function () {
  'use strict';
  const JP = window.JP;

  const PAGE_SIZE = 12;

  const state = {
    mode: 'library', // library | live
    jobs: [],
    loading: false,
    query: '',
    location: '',
    remote: false,
    category: '',
    workplace: '',
    seniority: '',
    employment: '',
    postedWithin: '',
    includeRelated: true,
    includeUnverified: true,
    saved: false,
    scored: false,
    minScore: 0,
    source: '',
    sort: 'score',
    page: 1,
    total: 0,
    totalPages: 1,
    meta: null,
    forceRefresh: false,
  };

  const el = (id) => document.getElementById(id);

  const TIER = {
    exact: { label: 'Exact match', tone: 'success' },
    related: { label: 'Related role', tone: 'cyan' },
    unverified: { label: 'Eligibility unverified', tone: 'warning' },
  };

  const WORKPLACE = {
    remote: { label: 'Remote', tone: 'cyan' },
    hybrid: { label: 'Hybrid', tone: 'accent' },
    onsite: { label: 'On-site', tone: 'neutral' },
  };

  // --- helpers ------------------------------------------------------------
  function badge(text, tone, iconName) {
    return `<span class="badge badge--${tone}">${iconName ? JP.icon(iconName, 11) : ''} ${JP.escapeHtml(text)}</span>`;
  }

  function workplaceBadge(job) {
    const key = job.workplace_type && job.workplace_type !== 'unknown' ? job.workplace_type : '';
    const info = WORKPLACE[key];
    if (!info) return '';
    return badge(info.label, info.tone, 'map-pin');
  }

  function eligibilityBadge(job) {
    if (job.workplace_type !== 'remote' && job.workplace_type !== 'hybrid') return '';
    const eligible = job.eligible_locations || [];
    if (eligible.length) {
      const shown = eligible.slice(0, 2).join(', ');
      const more = eligible.length > 2 ? ` +${eligible.length - 2}` : '';
      return badge(`Eligible: ${shown}${more}`, 'neutral', 'shield');
    }
    if (job.remote_scope === 'worldwide') return badge('Worldwide remote', 'success', 'shield');
    if (job.location_confidence === 'unknown') return badge('Eligibility unverified', 'warning', 'alert');
    return '';
  }

  function freshnessBadge(job) {
    if (job.availability === 'stale') return badge('Stale listing', 'warning', 'clock');
    if (!job.posted_at) return '';
    const posted = new Date(String(job.posted_at).replace('Z', '+00:00')).getTime();
    if (Number.isNaN(posted)) return '';
    if (Date.now() - posted < 7 * 86400000) return badge('New', 'success', 'zap');
    if (job.expires_at) {
      const expires = new Date(String(job.expires_at).replace('Z', '+00:00')).getTime();
      if (!Number.isNaN(expires) && expires - Date.now() < 7 * 86400000) {
        return badge('Expiring soon', 'warning', 'clock');
      }
    }
    return '';
  }

  function categoryBadge(job) {
    return job.category_label ? `<span class="job-card__meta-item">${JP.escapeHtml(job.category_label)}</span>` : '';
  }

  function skillChips(job) {
    const skills = (job.skills || []).slice(0, 5);
    const matched = new Set((job.matched_skills || []).map((s) => String(s).toLowerCase()));
    if (!skills.length) return '';
    return `<div class="job-card__skills">${skills
      .map(
        (skill) =>
          `<span class="chip ${matched.has(String(skill).toLowerCase()) ? 'chip--accent' : ''}">${JP.escapeHtml(skill)}</span>`
      )
      .join('')}${(job.skills || []).length > 5 ? `<span class="chip chip--muted">+${job.skills.length - 5}</span>` : ''}</div>`;
  }

  function jobCard(job) {
    const score = job.score === null || job.score === undefined ? 0 : job.score;
    const tier = TIER[job.match_tier] || TIER.exact;
    return `
      <article class="job-card" data-job="${JP.escapeAttr(job.id)}" tabindex="0" role="button" aria-label="${JP.escapeAttr(job.title)} at ${JP.escapeAttr(job.company || 'unknown company')}">
        <div class="job-card__head">
          <span class="company-logo">${JP.escapeHtml(JP.initials(job.company))}</span>
          <div class="job-card__head-main">
            <h3 class="job-card__title">${JP.escapeHtml(job.title)}</h3>
            <div class="job-card__company">
              <span>${JP.escapeHtml(job.company || 'Unknown')}</span>
              ${job.location ? `<span>·</span><span>${JP.escapeHtml(job.location)}</span>` : ''}
            </div>
          </div>
          <div class="job-card__score" title="Profile match score">
            ${JP.scoreRing(score, 44)}
          </div>
        </div>
        <div class="job-card__meta">
          ${badge(tier.label, tier.tone)}
          ${workplaceBadge(job)}
          ${eligibilityBadge(job)}
          ${freshnessBadge(job)}
          ${job.seniority && job.seniority !== 'unspecified' ? badge(job.seniority, 'neutral') : ''}
          ${job.salary ? `<span class="job-card__meta-item">${JP.icon('chart', 12)} ${JP.escapeHtml(job.salary)}</span>` : ''}
        </div>
        ${skillChips(job)}
        <div class="job-card__foot">
          <span class="job-card__source">
            ${JP.escapeHtml(job.source || 'manual')}
            ${job.posted_at ? ` · ${JP.escapeHtml(JP.timeAgo(job.posted_at))}` : ''}
            ${categoryBadge(job)}
          </span>
          <div class="job-card__actions">
            <button class="job-card__save ${job.saved ? 'is-saved' : ''}" type="button" data-save="${JP.escapeAttr(job.id)}"
              aria-label="${job.saved ? 'Unsave' : 'Save'} job" title="${job.saved ? 'Unsave' : 'Save'}">
              ${JP.icon('bookmark', 15)}
            </button>
            <button class="btn btn--secondary btn--xs" type="button" data-open="${JP.escapeAttr(job.id)}">View ${JP.icon('chevron-right', 12)}</button>
          </div>
        </div>
      </article>`;
  }

  function visibleJobs() {
    let jobs = state.jobs.slice();
    if (state.saved) jobs = jobs.filter((job) => job.saved);
    if (state.scored) jobs = jobs.filter((job) => job.score !== null && job.score !== undefined);
    if (state.minScore) jobs = jobs.filter((job) => Number(job.score || 0) >= state.minScore);
    return jobs;
  }

  function renderJobs() {
    const grid = el('jobs-grid');
    if (!grid) return;
    const jobs = visibleJobs();

    if (!jobs.length) {
      const isLive = state.mode === 'live';
      grid.innerHTML = JP.emptyState(
        'search',
        isLive ? 'No listing passed every filter' : 'Your job library is empty',
        isLive
          ? 'Sources responded but nothing matched this role, exact city and workplace combination. Try another role or workplace type, or search a different city explicitly. Nearby cities are not substituted.'
          : 'Search live sources or paste a job URL to start building your pipeline.',
        `<button class="btn btn--secondary btn--sm" type="button" id="empty-import">Import a job</button>`
      );
      el('empty-import')?.addEventListener('click', openImport);
    } else {
      grid.innerHTML = jobs.map(jobCard).join('');
      JP.$$('.job-card', grid).forEach((card) => {
        card.addEventListener('click', (event) => {
          if (event.target.closest('button')) return;
          openJob(card.dataset.job);
        });
        card.addEventListener('keydown', (event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            openJob(card.dataset.job);
          }
        });
      });
      JP.$$('[data-save]', grid).forEach((button) => {
        button.addEventListener('click', async (event) => {
          event.stopPropagation();
          await toggleSave(button.dataset.save, button, !button.classList.contains('is-saved'));
        });
      });
      JP.$$('[data-open]', grid).forEach((button) => {
        button.addEventListener('click', (event) => {
          event.stopPropagation();
          openJob(button.dataset.open);
        });
      });
    }

    const count = el('results-count');
    if (count) {
      const shown = jobs.length;
      const total = state.total || shown;
      const where = state.location ? ` in ${JP.escapeHtml(state.location)}` : '';
      const what = state.query ? ` matching “${JP.escapeHtml(state.query)}”` : '';
      const mode = state.mode === 'live' ? 'live sources' : 'your library';
      const filtered = shown !== state.jobs.length ? ` · ${shown} shown after library filters` : '';
      count.innerHTML = `<strong>${total}</strong> role${total === 1 ? '' : 's'}${what}${where} from ${mode}${filtered}`;
    }
    renderPager();
  }

  // --- pagination ---------------------------------------------------------
  function renderPager() {
    const pager = el('jobs-pager');
    const status = el('pager-status');
    if (!pager) return;
    const totalPages = Math.max(1, state.totalPages || 1);
    if (totalPages <= 1) {
      pager.hidden = true;
      return;
    }
    pager.hidden = false;
    if (status) status.textContent = `Page ${state.page} of ${totalPages} · ${state.total} role${state.total === 1 ? '' : 's'}`;
    const prev = el('pager-prev');
    const next = el('pager-next');
    if (prev) prev.disabled = state.page <= 1 || state.loading;
    if (next) next.disabled = state.page >= totalPages || state.loading;
  }

  async function changePage(delta) {
    const target = state.page + delta;
    const totalPages = Math.max(1, state.totalPages || 1);
    if (target < 1 || target > totalPages || state.loading) return;
    state.page = target;
    if (state.mode === 'live') {
      await liveSearch(null, target);
    } else {
      await loadLibrary(target);
    }
    document.querySelector('.results-bar')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // --- progress & activity ------------------------------------------------
  let progressTimer = null;
  let progressStarted = 0;

  function startProgress() {
    const wrap = el('search-progress');
    if (!wrap) return;
    wrap.hidden = false;
    progressStarted = performance.now();
    const timer = el('search-timer');
    if (timer) timer.textContent = '0.0s';
    clearInterval(progressTimer);
    progressTimer = setInterval(() => {
      const seconds = (performance.now() - progressStarted) / 1000;
      if (timer) timer.textContent = `${seconds.toFixed(1)}s`;
    }, 100);
    setActivity(true, 'Searching live sources');
  }

  function stopProgress() {
    clearInterval(progressTimer);
    progressTimer = null;
    const wrap = el('search-progress');
    if (wrap) wrap.hidden = true;
  }

  function setActivity(active, label) {
    const pill = el('activity-pill');
    const text = el('activity-label');
    if (pill) pill.classList.toggle('is-live', Boolean(active));
    if (text) text.textContent = label;
  }

  function setLoading(loading) {
    state.loading = loading;
    const button = el('jobs-submit');
    if (!button) return;
    button.disabled = loading;
    button.classList.toggle('is-loading', loading);
    renderPager();
  }

  // --- queries ------------------------------------------------------------
  function libraryQuery(page) {
    const params = new URLSearchParams();
    if (state.query) params.set('q', state.query);
    if (state.location) params.set('location', state.location);
    if (state.remote) params.set('remote', '1');
    if (state.saved) params.set('saved', '1');
    if (state.minScore) params.set('min_score', String(state.minScore));
    if (state.source) params.set('source', state.source);
    if (state.category) params.set('category', state.category);
    if (state.workplace) params.set('workplace', state.workplace);
    params.set('sort', state.sort);
    params.set('limit', String(PAGE_SIZE));
    params.set('offset', String((page - 1) * PAGE_SIZE));
    return `/api/jobs?${params.toString()}`;
  }

  async function loadLibrary(page = 1) {
    state.mode = 'library';
    state.page = page;
    state.forceRefresh = false;
    setLoading(true);
    stopProgress();
    setActivity(false, 'Saved library');
    try {
      const data = await JP.api(libraryQuery(page));
      state.jobs = data.jobs || [];
      state.total = Number.isFinite(data.total) ? data.total : state.jobs.length;
      state.totalPages = Math.max(1, Math.ceil(state.total / PAGE_SIZE));
      state.meta = null;
      renderJobs();
      renderMeta(null);
      setActivity(false, `${state.total} saved role${state.total === 1 ? '' : 's'}`);
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
      setActivity(false, 'Library unavailable');
      const grid = el('jobs-grid');
      if (grid) grid.innerHTML = JP.emptyState('alert', 'Could not load your library', error.message);
    } finally {
      setLoading(false);
    }
  }

  function liveBody(page) {
    return {
      query: state.query,
      location: state.location,
      remote_only: state.remote,
      category: state.category,
      workplace_type: state.workplace,
      seniority: state.seniority,
      employment_type: state.employment,
      include_related: state.includeRelated,
      include_unverified: state.includeUnverified,
      posted_within_days: state.postedWithin ? Number(state.postedWithin) : null,
      page,
      page_size: PAGE_SIZE,
      limit: PAGE_SIZE,
      refresh: state.forceRefresh,
    };
  }

  async function liveSearch(event, page = 1) {
    if (event) event.preventDefault();
    readFilters();
    state.mode = 'live';
    state.page = page;
    setLoading(true);
    startProgress();
    const grid = el('jobs-grid');
    if (grid) grid.innerHTML = JP.skeletonCards(4);
    const count = el('results-count');
    if (count) count.textContent = 'Searching every enabled source…';
    const metaWrap = el('results-meta');
    if (metaWrap) metaWrap.innerHTML = '';
    const notes = el('search-notes');
    if (notes) notes.hidden = true;

    const body = liveBody(page);
    state.forceRefresh = false;
    try {
      const data = await JP.api('/api/jobs/search', { method: 'POST', body });
      state.jobs = data.jobs || [];
      state.meta = data.meta || {};
      state.total = Number(data.total ?? data.count ?? state.jobs.length);
      state.totalPages = Number(data.total_pages || Math.max(1, Math.ceil(state.total / PAGE_SIZE)));
      state.page = Number(data.page || page);
      renderJobs();
      renderMeta(state.meta);
      const failed = (state.meta.source_runs || []).filter(
        (run) => run.status === 'error' || run.status === 'timeout'
      );
      if (state.total && failed.length) {
        JP.toast(
          `${state.total} roles matched. ${failed.length} source${failed.length === 1 ? '' : 's'} failed — results may be incomplete.`,
          { tone: 'info', title: 'Partial results' }
        );
      } else if (state.total) {
        JP.toast(`${state.total} roles matched ${state.page > 1 ? `(page ${state.page})` : ''}.`, { title: 'Search complete' });
      } else {
        JP.toast('No role passed the category and geography filters. See the notes under the results.', {
          tone: 'info',
          title: 'No verified matches',
        });
      }
      const ok = (state.meta.source_runs || []).filter((run) => run.status === 'ok').length;
      setActivity(false, `${ok} source${ok === 1 ? '' : 's'} · ${state.total} match${state.total === 1 ? '' : 'es'}`);
    } catch (error) {
      JP.toast(error.message, { tone: 'error', title: 'Search failed' });
      if (grid) grid.innerHTML = JP.emptyState('alert', 'Live search failed', error.message);
      if (count) count.textContent = 'Search failed';
      setActivity(false, 'Search failed');
    } finally {
      stopProgress();
      setLoading(false);
    }
  }

  // --- metadata -----------------------------------------------------------
  function renderMeta(meta) {
    const wrap = el('results-meta');
    if (!wrap) return;
    if (!meta || meta.library) {
      wrap.innerHTML = '<span class="badge badge--neutral">Saved library</span>';
      return;
    }
    const chips = [];
    const counts = meta.counts || {};
    if (counts.exact) chips.push(badge(`${counts.exact} exact`, 'success'));
    if (counts.related) chips.push(badge(`${counts.related} related`, 'cyan'));
    if (counts.unverified) chips.push(badge(`${counts.unverified} unverified`, 'warning'));
    (meta.source_runs || [])
      .filter((run) => run.status === 'ok')
      .slice(0, 4)
      .forEach((run) => chips.push(badge(`${run.name} ${run.count}`, 'neutral')));
    const failed = (meta.source_runs || []).filter(
      (run) => run.status === 'error' || run.status === 'timeout'
    );
    if (failed.length) chips.push(badge(`${failed.length} source${failed.length === 1 ? '' : 's'} failed`, 'warning', 'alert'));
    if (meta.duplicates) chips.push(badge(`${meta.duplicates} duplicates merged`, 'neutral'));
    if (meta.cache && meta.cache.hit) chips.push(badge(`Cached ${Math.round(meta.cache.age_seconds || 0)}s ago`, 'neutral', 'database'));
    if (meta.elapsed_ms) chips.push(badge(`${(Number(meta.elapsed_ms) / 1000).toFixed(1)}s`, 'neutral'));
    wrap.innerHTML = chips.join('');

    const notes = el('search-notes');
    const body = el('search-notes-body');
    if (!notes || !body) return;
    const lines = [];
    const interpreted = meta.interpreted || {};
    if (interpreted.category_label) lines.push(`Category: ${interpreted.category_label}.`);
    if (interpreted.location) lines.push(`Geography: ${interpreted.location}.`);
    else lines.push('Geography: no location restriction was applied.');
    if (interpreted.workplace_type && interpreted.workplace_type !== 'any') lines.push(`Workplace: ${interpreted.workplace_type}.`);
    if (interpreted.seniority) lines.push(`Seniority: ${interpreted.seniority}.`);
    if (interpreted.employment_type) lines.push(`Employment: ${interpreted.employment_type}.`);
    const freshness = meta.freshness || {};
    if (freshness.newest_posted) {
      lines.push(`Newest posting: ${JP.formatDate(freshness.newest_posted)} · active ${freshness.active || 0} · stale ${freshness.stale || 0} · unknown age ${freshness.unknown || 0}.`);
    }
    (meta.notes || []).forEach((note) => lines.push(note));

    const runs = meta.source_runs || [];
    const runRows = runs
      .map(
        (run) => `<tr>
          <td>${JP.escapeHtml(run.name)}</td>
          <td><span class="badge badge--${run.status === 'ok' ? 'success' : run.status === 'empty' ? 'neutral' : run.status === 'error' || run.status === 'timeout' ? 'warning' : 'neutral'}">${JP.escapeHtml(run.status)}</span></td>
          <td>${run.count || 0}</td>
          <td>${run.elapsed_ms ? `${Math.round(run.elapsed_ms)}ms` : '—'}</td>
          <td>${JP.escapeHtml(run.error || '')}</td>
        </tr>`
      )
      .join('');
    body.innerHTML = `
      <ul class="notes-list">${lines.map((line) => `<li>${JP.escapeHtml(line)}</li>`).join('')}</ul>
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr><th>Source</th><th>Status</th><th>Fetched</th><th>Time</th><th>Detail</th></tr></thead>
          <tbody>${runRows}</tbody>
        </table>
      </div>`;
    notes.hidden = false;
  }

  // --- save ---------------------------------------------------------------
  async function toggleSave(jobId, button, saved) {
    try {
      await JP.api(`/api/jobs/${encodeURIComponent(jobId)}/save`, {
        method: 'POST',
        body: { saved },
      });
      const job = state.jobs.find((item) => item.id === jobId);
      if (job) job.saved = saved;
      if (button) {
        button.classList.toggle('is-saved', saved);
        button.setAttribute('aria-label', saved ? 'Unsave job' : 'Save job');
        button.title = saved ? 'Unsave' : 'Save';
      }
      const drawerButton = el('drawer-save');
      if (drawerButton && drawerButton.dataset.job === jobId) {
        drawerButton.dataset.saved = saved ? '1' : '0';
        drawerButton.innerHTML = `${JP.icon('bookmark', 14)} ${saved ? 'Saved' : 'Save job'}`;
      }
      JP.toast(saved ? 'Saved to your library.' : 'Removed from saved roles.', { tone: 'info', duration: 2600 });
      if (state.saved) renderJobs();
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
    }
  }

  // --- drawer -------------------------------------------------------------
  const drawer = () => el('job-drawer');
  const backdrop = () => el('job-backdrop');

  function closeDrawer() {
    drawer()?.classList.remove('is-open');
    backdrop()?.classList.remove('is-open');
    drawer()?.setAttribute('aria-hidden', 'true');
  }

  function breakdownRows(breakdown) {
    const order = ['skills', 'role', 'semantic', 'location', 'seniority', 'recency'];
    return order
      .filter((key) => breakdown && key in breakdown)
      .map((key) => {
        const value = Math.round((Number(breakdown[key]) || 0) * 100);
        return `<div class="breakdown-row">
          <span class="breakdown-row__label">${key}</span>
          <div class="progress"><span style="width:${value}%"></span></div>
          <span class="breakdown-row__value">${value}</span>
        </div>`;
      })
      .join('');
  }

  function chips(skills, tone) {
    if (!skills || !skills.length) return '<span class="chip chip--muted">None detected</span>';
    return skills.map((skill) => `<span class="chip ${tone ? `chip--${tone}` : ''}">${JP.escapeHtml(skill)}</span>`).join('');
  }

  function detailRow(label, value) {
    return `<div class="run-meta"><span>${JP.escapeHtml(label)}</span><strong>${JP.escapeHtml(value || '—')}</strong></div>`;
  }

  async function openJob(jobId) {
    const panel = drawer();
    const shade = backdrop();
    if (!panel || !shade) return;
    shade.classList.add('is-open');
    panel.classList.add('is-open');
    panel.setAttribute('aria-hidden', 'false');
    panel.innerHTML = `<div class="drawer__body"><div class="skeleton skeleton--title"></div><div class="skeleton skeleton--line"></div><div class="skeleton skeleton--line"></div><div class="skeleton skeleton--line"></div></div>`;
    try {
      const data = await JP.api(`/api/jobs/${encodeURIComponent(jobId)}`);
      const job = data.job || {};
      const match = data.match || {};
      const application = data.application;
      const saved = Boolean(job.saved);
      const tier = TIER[job.match_tier] || TIER.exact;
      const eligible = (job.eligible_locations || []).join(', ');
      const reasons = (job.relevance_reasons || [])
        .map((reason) => `<li>${JP.escapeHtml(reason)}</li>`)
        .join('');
      panel.innerHTML = `
        <div class="drawer__head">
          <span class="company-logo company-logo--lg company-logo--accent">${JP.escapeHtml(JP.initials(job.company))}</span>
          <div class="drawer__head-main">
            <h2>${JP.escapeHtml(job.title)}</h2>
            <p>${JP.escapeHtml(job.company || 'Unknown')}${job.location ? ` · ${JP.escapeHtml(job.location)}` : ''}</p>
            <div class="chip-list mt-8">
              ${badge(tier.label, tier.tone)}
              ${workplaceBadge(job)}
              ${eligibilityBadge(job)}
              ${freshnessBadge(job)}
            </div>
          </div>
          <button class="icon-btn" type="button" data-close-drawer aria-label="Close">${JP.icon('x', 18)}</button>
        </div>
        <div class="drawer__body">
          <div class="row-12 wrap">
            ${JP.scoreRing(match.score, 58)}
            <div style="flex:1;min-width:180px">
              <div class="section-label">Profile match rationale</div>
              <p class="small muted mt-8" style="line-height:1.6">${JP.escapeHtml(match.rationale || 'No rationale available yet.')}</p>
            </div>
          </div>
          <div class="drawer__section">
            <h3>Why this appeared</h3>
            ${reasons ? `<ul class="notes-list">${reasons}</ul>` : '<p class="small muted">No relevance notes were recorded.</p>'}
          </div>
          <div class="drawer__section">
            <h3>Score breakdown</h3>
            ${breakdownRows(match.breakdown || job.score_breakdown)}
          </div>
          <div class="drawer__section">
            <h3>Matched skills</h3>
            <div class="chip-list">${chips(match.matched_skills, 'accent')}</div>
          </div>
          <div class="drawer__section">
            <h3>Gaps to address</h3>
            <div class="chip-list">${chips(match.missing_skills)}</div>
          </div>
          <div class="drawer__section">
            <h3>Role &amp; eligibility</h3>
            <div class="run-meta-grid">
              ${detailRow('Category', job.category_label || job.category)}
              ${detailRow('Workplace', job.workplace_type)}
              ${detailRow('Job location', job.job_location || job.location)}
              ${detailRow('Company location', job.company_location)}
              ${detailRow('Eligible locations', eligible)}
              ${detailRow('Remote scope', job.remote_scope)}
              ${detailRow('Location confidence', job.location_confidence)}
              ${detailRow('Availability', job.availability)}
              ${detailRow('Seniority', job.seniority || 'unspecified')}
              ${detailRow('Salary', job.salary || 'not listed')}
              ${detailRow('Source', job.source || 'manual')}
              ${detailRow('Posted', JP.formatDate(job.posted_at))}
              ${detailRow('Expires', job.expires_at ? JP.formatDate(job.expires_at) : '')}
            </div>
          </div>
          <div class="drawer__section">
            <h3>Description</h3>
            <div class="job-description">${JP.escapeHtml(job.description || 'This source did not include a description. Open the original posting for details.')}</div>
          </div>
          ${application ? `<div class="drawer__section"><h3>Application</h3><div class="approval-card" style="border-color:var(--border);background:var(--surface)"><div class="approval-card__head" style="color:var(--text-soft)">${JP.icon('kanban', 14)} Tracked as ${JP.escapeHtml(application.status)}</div><p>Added ${JP.escapeHtml(JP.timeAgo(application.created_at))}. Manage it in the tracker.</p><div class="approval-card__actions"><a class="btn btn--secondary btn--xs" href="/tracker">Open tracker</a></div></div></div>` : ''}
        </div>
        <div class="drawer__foot">
          <button class="btn btn--secondary btn--sm" type="button" id="drawer-save" data-job="${JP.escapeAttr(job.id)}" data-saved="${saved ? '1' : '0'}">
            ${JP.icon('bookmark', 14)} ${saved ? 'Saved' : 'Save job'}
          </button>
          <button class="btn btn--outline btn--sm" type="button" id="drawer-tailor">${JP.icon('wand', 14)} Tailor CV</button>
          <button class="btn btn--ghost btn--sm" type="button" id="drawer-letter">${JP.icon('file-text', 14)} Cover letter</button>
          <button class="btn btn--success btn--sm" type="button" id="drawer-track">${JP.icon('plus', 14)} Track application</button>
          ${job.url ? `<a class="btn btn--ghost btn--sm" href="${JP.escapeAttr(job.url)}" target="_blank" rel="noopener noreferrer">${JP.icon('external', 14)} Original</a>` : ''}
        </div>`;

      panel.querySelector('[data-close-drawer]').addEventListener('click', closeDrawer);
      el('drawer-save').addEventListener('click', () => {
        const button = el('drawer-save');
        toggleSave(job.id, button, button.dataset.saved !== '1');
      });
      el('drawer-tailor').addEventListener('click', () => tailor(job.id));
      el('drawer-letter').addEventListener('click', () => coverLetter(job.id));
      el('drawer-track').addEventListener('click', () => track(job.id));
    } catch (error) {
      panel.innerHTML = `<div class="drawer__head"><div class="drawer__head-main"><h2>Job unavailable</h2><p>${JP.escapeHtml(error.message)}</p></div><button class="icon-btn" type="button" data-close-drawer aria-label="Close">${JP.icon('x', 18)}</button></div>`;
      panel.querySelector('[data-close-drawer]').addEventListener('click', closeDrawer);
    }
  }

  // --- actions ------------------------------------------------------------
  async function track(jobId) {
    const button = el('drawer-track');
    if (button) {
      button.disabled = true;
      button.classList.add('is-loading');
    }
    try {
      await JP.api('/api/applications', { method: 'POST', body: { job_id: jobId, status: 'applied' } });
      JP.toast('Added to your tracker as applied.', { title: 'Application tracked' });
      if (button) {
        button.classList.remove('is-loading');
        button.innerHTML = `${JP.icon('check', 14)} Tracked`;
      }
      JP.refreshApprovalCount();
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
      if (button) {
        button.disabled = false;
        button.classList.remove('is-loading');
      }
    }
  }

  function cvText(cv) {
    const lines = [];
    if (cv.headline) lines.push(cv.headline, '');
    if (cv.summary) lines.push(cv.summary, '');
    if (cv.skills && cv.skills.length) lines.push(`Skills: ${cv.skills.join(', ')}`, '');
    (cv.bullets || []).forEach((bullet) => lines.push(`• ${bullet.text}`));
    return lines.join('\n');
  }

  async function tailor(jobId) {
    const button = el('drawer-tailor');
    if (button) button.classList.add('is-loading');
    try {
      const data = await JP.api('/api/tailor/cv', { method: 'POST', body: { job_id: jobId } });
      const cv = data.cv || {};
      const bullets = (cv.bullets || [])
        .map(
          (bullet) => `<div class="quality-item">
            <span style="color:var(--${bullet.supported ? 'green' : 'red'})">${JP.icon(bullet.supported ? 'check' : 'alert', 14)}</span>
            <div><strong>${JP.escapeHtml(bullet.text)}</strong><small>${JP.escapeHtml(bullet.evidence || 'No evidence quoted')}</small></div>
          </div>`
        )
        .join('');
      const changes = (cv.changes || [])
        .map(
          (change) => `<div class="quality-item">
            <span style="color:var(--green)">${JP.icon('check', 14)}</span>
            <div><strong>${JP.escapeHtml(change.label || change.field || 'Update')}</strong><small>${JP.escapeHtml(change.detail || '')}</small></div>
          </div>`
        )
        .join('');
      const downloads = (data.downloads || [])
        .map(
          (item) => `<a class="btn btn--secondary" href="${JP.escapeAttr(item.url)}" download>${JP.icon('download', 14)} ${JP.escapeHtml(String(item.format || '').toUpperCase())}</a>`
        )
        .join('');
      JP.modal({
        title: 'Verified CV draft',
        subtitle: `${cv.unsupported_count || 0} unsupported claim${cv.unsupported_count === 1 ? '' : 's'} · method: ${cv.method || 'template'}`,
        wide: true,
        body: `
          <div class="chip-list">
            <span class="badge badge--${cv.verified ? 'success' : 'warning'}">${cv.verified ? 'All claims supported' : 'Review needed'}</span>
            <span class="badge badge--neutral">${(cv.bullets || []).length} bullet${(cv.bullets || []).length === 1 ? '' : 's'}</span>
            <span class="badge badge--neutral">${(cv.skills || []).length} skill${(cv.skills || []).length === 1 ? '' : 's'}</span>
          </div>
          <div>
            <div class="section-label">Target job</div>
            <p class="strong mt-8">${JP.escapeHtml((cv.job && cv.job.title) || 'This role')} · ${JP.escapeHtml((cv.job && cv.job.company) || '')}</p>
          </div>
          <div>
            <div class="section-label">What changed</div>
            <div class="quality-list mt-8">${changes || '<p class="small muted">No change summary recorded.</p>'}</div>
          </div>
          <div>
            <div class="section-label">Headline</div>
            <p class="strong mt-8">${JP.escapeHtml(cv.headline || '')}</p>
          </div>
          <div>
            <div class="section-label">Summary</div>
            <p class="small muted mt-8" style="line-height:1.65">${JP.escapeHtml(cv.summary || '')}</p>
          </div>
          <div>
            <div class="section-label">Skills</div>
            <div class="chip-list mt-8">${(cv.skills || []).map((s) => `<span class="chip chip--accent">${JP.escapeHtml(s)}</span>`).join('')}</div>
          </div>
          <div>
            <div class="section-label">Evidence-checked bullets</div>
            <div class="quality-list mt-8">${bullets || '<p class="small muted">No bullets could be matched to your profile.</p>'}</div>
          </div>
          <p class="field-hint">The original upload is untouched. Download the tailored file below, or open <a href="/drafts" style="color:var(--accent)">Drafts</a> later.</p>`,
        footer: `<button class="btn btn--ghost" type="button" data-modal-close>Close</button>
          ${downloads}
          <button class="btn btn--secondary" type="button" id="cv-copy">${JP.icon('copy', 14)} Copy text</button>
          <button class="btn btn--primary" type="button" id="cv-track">${JP.icon('plus', 14)} Track application</button>`,
        onMount(root) {
          root.querySelector('#cv-copy').addEventListener('click', () => JP.copyText(cvText(cv)));
          root.querySelector('#cv-track').addEventListener('click', () => {
            JP.closeModal();
            track(jobId);
          });
        },
      });
    } catch (error) {
      JP.toast(error.message, { tone: 'error', title: 'Tailoring failed' });
    } finally {
      if (button) button.classList.remove('is-loading');
    }
  }

  async function coverLetter(jobId) {
    const button = el('drawer-letter');
    if (button) button.classList.add('is-loading');
    try {
      const data = await JP.api('/api/tailor/cover-letter', { method: 'POST', body: { job_id: jobId } });
      const letter = data.cover_letter || {};
      JP.modal({
        title: 'Cover letter draft',
        subtitle: `${letter.unsupported_count || 0} unsupported claim${letter.unsupported_count === 1 ? '' : 's'} · verified against your profile`,
        wide: true,
        body: `<div class="job-description" style="max-height:52vh">${JP.escapeHtml(letter.body || '')}</div>
          <div class="chip-list"><span class="badge badge--${letter.verified ? 'success' : 'warning'}">${letter.verified ? 'Verified' : 'Needs review'}</span>
          <span class="badge badge--neutral">${(letter.checks || []).length} claims checked</span></div>`,
        footer: `<button class="btn btn--ghost" type="button" data-modal-close>Close</button>
          <button class="btn btn--primary" type="button" id="letter-copy">${JP.icon('copy', 14)} Copy letter</button>`,
        onMount(root) {
          root.querySelector('#letter-copy').addEventListener('click', () => JP.copyText(letter.body || ''));
        },
      });
    } catch (error) {
      JP.toast(error.message, { tone: 'error', title: 'Cover letter failed' });
    } finally {
      if (button) button.classList.remove('is-loading');
    }
  }

  function openImport() {
    JP.modal({
      title: 'Import a job',
      subtitle: 'Paste a posting URL or the full description. JobPilot extracts the rest.',
      body: `<div class="import-zone"><textarea class="textarea" id="import-value" placeholder="https://company.com/jobs/senior-ai-engineer&#10;&#10;Or paste the whole job description here…"></textarea></div>
        <p class="field-hint">Nothing is submitted anywhere. The posting is parsed locally and added to your library.</p>`,
      footer: `<button class="btn btn--ghost" type="button" data-modal-close>Cancel</button>
        <button class="btn btn--primary" type="button" id="import-submit">${JP.icon('plus', 14)} Import job</button>`,
      onMount(root) {
        const value = root.querySelector('#import-value');
        value.focus();
        root.querySelector('#import-submit').addEventListener('click', async () => {
          const text = value.value.trim();
          if (!text) {
            JP.toast('Paste a URL or description first.', { tone: 'error' });
            return;
          }
          const button = root.querySelector('#import-submit');
          button.classList.add('is-loading');
          try {
            const data = await JP.api('/api/jobs/import', { method: 'POST', body: { value: text } });
            JP.closeModal();
            JP.toast(`Imported “${data.job.title}”.`, { title: 'Job added' });
            state.jobs = [data.job, ...state.jobs];
            renderJobs();
            openJob(data.job.id);
          } catch (error) {
            JP.toast(error.message, { tone: 'error' });
            button.classList.remove('is-loading');
          }
        });
      },
    });
  }

  // --- filters ------------------------------------------------------------
  function readFilters() {
    state.query = el('jobs-query').value.trim();
    state.location = el('jobs-location').value.trim();
    state.remote = el('jobs-remote').checked;
    state.category = el('filter-category').value;
    state.workplace = state.remote ? 'remote' : el('filter-workplace').value;
    state.seniority = el('filter-seniority').value;
    state.employment = el('filter-employment').value;
    state.postedWithin = el('filter-posted').value;
    state.includeRelated = el('filter-related').checked;
    state.includeUnverified = el('filter-unverified').checked;
    state.saved = el('filter-saved').checked;
    state.scored = el('filter-scored').checked;
    state.minScore = Number(el('filter-score').value);
    state.source = el('filter-source').value;
    state.sort = el('filter-sort').value;
  }

  function resetFilters() {
    el('jobs-query').value = '';
    el('jobs-location').value = '';
    el('jobs-remote').checked = false;
    el('filter-category').value = '';
    el('filter-workplace').value = '';
    el('filter-seniority').value = '';
    el('filter-employment').value = '';
    el('filter-posted').value = '';
    el('filter-related').checked = true;
    el('filter-unverified').checked = true;
    el('filter-saved').checked = false;
    el('filter-scored').checked = false;
    el('filter-score').value = '0';
    el('filter-score-value').textContent = '0';
    el('filter-source').value = '';
    el('filter-sort').value = 'score';
    readFilters();
    loadLibrary(1);
  }

  function applyFilters() {
    readFilters();
    if (state.mode === 'live') {
      liveSearch(null, 1);
    } else {
      loadLibrary(1);
    }
  }

  async function loadSources() {
    try {
      const data = await JP.api('/api/sources');
      const select = el('filter-source');
      const sources = data.sources || [];
      if (select) {
        sources.forEach((source) => {
          const option = document.createElement('option');
          option.value = source.name;
          option.textContent = `${source.name} (${source.status})`;
          select.appendChild(option);
        });
      }
      const enabled = sources.filter((source) => source.enabled).length;
      if (!state.loading) setActivity(false, `${enabled} source${enabled === 1 ? '' : 's'} enabled`);
    } catch (error) {
      /* filters still work without the source list */
    }
  }

  // --- boot ---------------------------------------------------------------
  function boot() {
    el('jobs-form')?.addEventListener('submit', (event) => liveSearch(event, 1));
    el('jobs-refresh')?.addEventListener('click', () => {
      readFilters();
      state.forceRefresh = true;
      if (!state.query && !state.location) {
        JP.toast('Add a role or location first, then run a fresh live search.', { tone: 'info' });
        return;
      }
      liveSearch(null, 1);
    });
    el('filter-apply')?.addEventListener('click', applyFilters);
    el('filter-reset')?.addEventListener('click', resetFilters);
    el('filter-score')?.addEventListener('input', (event) => {
      el('filter-score-value').textContent = event.target.value;
    });
    el('filter-saved')?.addEventListener('change', () => {
      readFilters();
      if (state.mode === 'library') loadLibrary(1);
      else renderJobs();
    });
    el('filter-scored')?.addEventListener('change', () => {
      readFilters();
      if (state.mode === 'library') loadLibrary(1);
      else renderJobs();
    });
    el('filter-score')?.addEventListener('change', () => {
      readFilters();
      if (state.mode === 'library') loadLibrary(1);
      else renderJobs();
    });
    el('filter-category')?.addEventListener('change', () => {
      if (state.mode === 'library') {
        readFilters();
        loadLibrary(1);
      }
    });
    el('filter-workplace')?.addEventListener('change', () => {
      if (state.mode === 'library') {
        readFilters();
        loadLibrary(1);
      }
    });
    el('filter-sort')?.addEventListener('change', () => {
      readFilters();
      if (state.mode === 'library') loadLibrary(1);
    });
    el('pager-prev')?.addEventListener('click', () => changePage(-1));
    el('pager-next')?.addEventListener('click', () => changePage(1));
    el('import-btn')?.addEventListener('click', openImport);
    backdrop()?.addEventListener('click', closeDrawer);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeDrawer();
    });

    loadSources();
    loadLibrary(1).then(() => {
      const params = new URLSearchParams(window.location.search);
      const jobId = params.get('job');
      if (jobId) openJob(jobId);
      const q = params.get('q');
      const place = params.get('location');
      if (q || place) {
        if (q) el('jobs-query').value = q;
        if (place) el('jobs-location').value = place;
        liveSearch(null, 1);
      }
    });
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
