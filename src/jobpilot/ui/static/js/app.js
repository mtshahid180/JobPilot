/* ==========================================================================
   JobPilot — core UI runtime.
   Theme, navigation, toasts, API helper, command palette, approvals drawer.
   No build step: plain ES2020, loaded before page modules.
   ========================================================================== */
(function () {
  'use strict';

  const meta = window.JOBPILOT || {};
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  // --- tiny helpers -------------------------------------------------------
  const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

  function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value).replace(/[&<>"']/g, (ch) => ESCAPES[ch]);
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/`/g, '&#96;');
  }

  function icon(name, size = 16, cls = '') {
    return (
      `<svg class="icon ${cls}" width="${size}" height="${size}" aria-hidden="true">` +
      `<use href="#icon-${escapeAttr(name)}"></use></svg>`
    );
  }

  function initials(name) {
    const parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return 'JP';
    return parts.slice(0, 2).map((part) => part[0].toUpperCase()).join('');
  }

  function debounce(fn, wait = 250) {
    let timer = null;
    return function debounced(...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), wait);
    };
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function formatDate(value) {
    if (!value) return '—';
    const date = new Date(String(value).replace('Z', '+00:00'));
    if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function timeAgo(value) {
    if (!value) return '—';
    const date = new Date(String(value).replace('Z', '+00:00'));
    if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
    const seconds = Math.max(0, (Date.now() - date.getTime()) / 1000);
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  function scoreRing(score, size = 48) {
    const value = Math.round(Number(score) || 0);
    const tone = value >= 75 ? 'high' : value >= 50 ? 'mid' : 'low';
    return (
      `<span class="score-ring score-ring--${tone}" style="--score:${clamp(value, 0, 100)};--size:${size}px">` +
      `<span class="score-ring__value">${value}</span></span>`
    );
  }

  function statusTone(status) {
    const map = {
      applied: 'cyan',
      screening: 'accent',
      interview: 'warning',
      offer: 'success',
      rejected: 'danger',
      withdrawn: 'neutral',
      saved: 'neutral',
      completed: 'success',
      failed: 'danger',
      running: 'accent',
      awaiting_approval: 'warning',
      active: 'success',
      pending: 'warning',
      superseded: 'neutral',
      forgotten: 'neutral',
    };
    return map[status] || 'neutral';
  }

  function emptyState(iconName, title, text, actionHtml = '') {
    return (
      '<div class="empty-state">' +
      `<div class="empty-state__icon">${icon(iconName, 26)}</div>` +
      `<h3>${escapeHtml(title)}</h3><p>${escapeHtml(text)}</p>${actionHtml}</div>`
    );
  }

  function skeletonCards(count = 4, lines = 3) {
    return Array.from({ length: count })
      .map(
        () =>
          '<div class="skeleton-card"><div class="skeleton skeleton--title"></div>' +
          Array.from({ length: lines }).map(() => '<div class="skeleton skeleton--line"></div>').join('') +
          '</div>'
      )
      .join('');
  }

  // --- API ----------------------------------------------------------------
  class ApiError extends Error {
    constructor(message, status, payload) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.payload = payload || {};
    }
  }

  async function api(path, options = {}) {
    const { method = 'GET', body, signal, raw } = options;
    const init = { method, signal, headers: { Accept: 'application/json' } };
    if (body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(path, init);
    } catch (error) {
      if (error.name === 'AbortError') throw error;
      throw new ApiError('Cannot reach the JobPilot server. Is it running?', 0, {});
    }
    let text = '';
    try {
      text = await response.text();
    } catch (error) {
      text = '';
    }
    let data = {};
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (error) {
        data = { ok: false, error: text.slice(0, 300) };
      }
    }
    if (raw) return { response, data };
    if (!response.ok || data.ok === false) {
      throw new ApiError(data.error || `Request failed (${response.status})`, response.status, data);
    }
    return data;
  }

  // --- toasts -------------------------------------------------------------
  function toast(message, options = {}) {
    const stack = $('#toast-stack');
    if (!stack) return;
    const tone = options.tone || 'success';
    const title = options.title || (tone === 'error' ? 'Something went wrong' : tone === 'info' ? 'Heads up' : 'Done');
    const node = document.createElement('div');
    node.className = `toast toast--${tone}`;
    node.setAttribute('role', tone === 'error' ? 'alert' : 'status');
    const iconName = tone === 'error' ? 'alert' : tone === 'info' ? 'sparkles' : 'check';
    node.innerHTML =
      `<span class="toast__icon">${icon(iconName, 15)}</span>` +
      `<div class="toast__body"><div class="toast__title">${escapeHtml(title)}</div>` +
      `<div class="toast__msg">${escapeHtml(message)}</div></div>` +
      `<button class="toast__close" type="button" aria-label="Dismiss">${icon('x', 14)}</button>`;
    const remove = () => {
      node.classList.add('is-leaving');
      setTimeout(() => node.remove(), 260);
    };
    $('.toast__close', node).addEventListener('click', remove);
    stack.appendChild(node);
    if (options.duration !== 0) setTimeout(remove, options.duration || 4800);
    return node;
  }

  // --- modal --------------------------------------------------------------
  const modalRoot = () => $('#modal-root');

  function closeModal() {
    const root = modalRoot();
    if (root) root.innerHTML = '';
    document.body.style.removeProperty('overflow');
  }

  function modal(options = {}) {
    const root = modalRoot();
    if (!root) return null;
    root.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true" aria-label="${escapeAttr(options.title || 'Dialog')}">
        <div class="modal__backdrop" data-modal-close></div>
        <div class="modal__panel ${options.wide ? 'modal__panel--wide' : ''} ${options.narrow ? 'modal__panel--narrow' : ''}">
          <div class="modal__head">
            <div class="modal__head-main">
              <h2>${escapeHtml(options.title || '')}</h2>
              ${options.subtitle ? `<p>${escapeHtml(options.subtitle)}</p>` : ''}
            </div>
            <button class="icon-btn" type="button" data-modal-close aria-label="Close">${icon('x', 18)}</button>
          </div>
          <div class="modal__body">${options.body || ''}</div>
          ${options.footer === null ? '' : `<div class="modal__foot">${options.footer || ''}</div>`}
        </div>
      </div>`;
    document.body.style.overflow = 'hidden';
    $$('[data-modal-close]', root).forEach((el) => el.addEventListener('click', closeModal));
    if (typeof options.onMount === 'function') options.onMount(root);
    return root;
  }

  function confirmDialog(options = {}) {
    return new Promise((resolve) => {
      const root = modal({
        title: options.title || 'Are you sure?',
        subtitle: options.subtitle || '',
        narrow: true,
        body: `<p class="small muted" style="line-height:1.65">${escapeHtml(options.message || '')}</p>`,
        footer:
          `<button class="btn btn--ghost" type="button" data-cancel>${escapeHtml(options.cancelLabel || 'Cancel')}</button>` +
          `<button class="btn ${options.danger ? 'btn--danger' : 'btn--primary'}" type="button" data-confirm>${escapeHtml(options.confirmLabel || 'Confirm')}</button>`,
        onMount(container) {
          container.querySelector('[data-cancel]').addEventListener('click', () => {
            closeModal();
            resolve(false);
          });
          container.querySelector('[data-confirm]').addEventListener('click', () => {
            closeModal();
            resolve(true);
          });
          container.querySelector('.modal__backdrop').addEventListener('click', () => resolve(false));
        },
      });
      if (!root) resolve(false);
    });
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast('Copied to clipboard.', { tone: 'info', title: 'Copied' });
      return true;
    } catch (error) {
      toast('Clipboard access was blocked by the browser.', { tone: 'error' });
      return false;
    }
  }

  // --- theme + sidebar ----------------------------------------------------
  function initTheme() {
    const button = $('#theme-toggle');
    if (!button) return;
    button.addEventListener('click', () => {
      const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
      document.documentElement.dataset.theme = next;
      try {
        localStorage.setItem('jobpilot-theme', next);
      } catch (error) {
        /* storage may be unavailable */
      }
    });
  }

  function initSidebar() {
    const sidebar = $('#sidebar');
    const toggle = $('#sidebar-toggle');
    const overlay = $('#sidebar-overlay');
    if (!sidebar || !toggle) return;
    const close = () => {
      sidebar.classList.remove('is-open');
      if (overlay) overlay.classList.remove('is-open');
    };
    toggle.addEventListener('click', () => {
      const open = !sidebar.classList.contains('is-open');
      sidebar.classList.toggle('is-open', open);
      if (overlay) overlay.classList.toggle('is-open', open);
    });
    if (overlay) overlay.addEventListener('click', close);
    $$('.nav-link', sidebar).forEach((link) => link.addEventListener('click', close));
  }

  // --- command palette ----------------------------------------------------
  const PAGES = [
    { label: 'Dashboard', hint: 'Overview', href: '/', icon: 'home', keys: 'home overview stats' },
    { label: 'Job explorer', hint: 'Search jobs', href: '/jobs', icon: 'briefcase', keys: 'jobs search explore' },
    { label: 'Agent console', hint: 'Chat', href: '/agent', icon: 'sparkles', keys: 'agent chat assistant' },
    { label: 'Tracker', hint: 'Applications', href: '/tracker', icon: 'kanban', keys: 'tracker pipeline applications' },
    { label: 'Drafts', hint: 'CVs & letters', href: '/drafts', icon: 'file-text', keys: 'drafts cv cover letter' },
    { label: 'Profile', hint: 'CV data', href: '/profile', icon: 'user', keys: 'profile cv resume' },
    { label: 'Memory', hint: 'Preferences', href: '/memory', icon: 'brain', keys: 'memory preferences facts' },
    { label: 'Runs & traces', hint: 'Observability', href: '/runs', icon: 'activity', keys: 'runs traces logs' },
    { label: 'Settings', hint: 'System', href: '/settings', icon: 'settings', keys: 'settings config sources tools' },
  ];

  const ACTIONS = [
    { label: 'Search jobs for my target role', hint: 'Agent', icon: 'search', kind: 'agent', prompt: 'Find remote Python AI engineer jobs and rank them for me.' },
    { label: 'Tailor my CV to the best match', hint: 'Agent', icon: 'wand', kind: 'agent', prompt: 'Tailor my CV to the best matching job and verify every claim.' },
    { label: 'Review pending approvals', hint: 'Safety', icon: 'shield', kind: 'approvals' },
    { label: 'Toggle theme', hint: 'Appearance', icon: 'moon', kind: 'theme' },
    { label: 'Check job sources', hint: 'Health', icon: 'server', kind: 'sources' },
  ];

  function initCommandPalette() {
    const palette = $('#command-palette');
    const input = $('#command-input');
    const list = $('#command-list');
    if (!palette || !input || !list) return;
    let index = 0;
    let results = [];

    const all = () => PAGES.map((p) => ({ ...p, kind: 'page' })).concat(ACTIONS);

    function render() {
      const query = input.value.trim().toLowerCase();
      results = all().filter((item) => {
        if (!query) return true;
        const haystack = `${item.label} ${item.hint} ${item.keys || ''}`.toLowerCase();
        return query.split(/\s+/).every((word) => haystack.includes(word));
      });
      index = clamp(index, 0, Math.max(0, results.length - 1));
      if (!results.length) {
        list.innerHTML = '<div class="list-empty">No matching pages or actions.</div>';
        return;
      }
      list.innerHTML = results
        .map(
          (item, i) =>
            `<button class="command-item ${i === index ? 'is-active' : ''}" type="button" data-index="${i}">` +
            `${icon(item.icon, 16)}<span>${escapeHtml(item.label)}</span><small>${escapeHtml(item.hint)}</small></button>`
        )
        .join('');
      $$('.command-item', list).forEach((button) => {
        button.addEventListener('click', () => activate(Number(button.dataset.index)));
        button.addEventListener('mousemove', () => {
          index = Number(button.dataset.index);
          $$('.command-item', list).forEach((el, i) => el.classList.toggle('is-active', i === index));
        });
      });
    }

    function open() {
      palette.hidden = false;
      index = 0;
      input.value = '';
      render();
      setTimeout(() => input.focus(), 20);
    }

    function close() {
      palette.hidden = true;
    }

    function activate(i) {
      const item = results[i];
      if (!item) return;
      close();
      if (item.kind === 'page') {
        window.location.href = item.href;
      } else if (item.kind === 'agent') {
        try {
          sessionStorage.setItem('jobpilot:agent-prompt', item.prompt);
        } catch (error) {
          /* ignore */
        }
        window.location.href = '/agent';
      } else if (item.kind === 'approvals') {
        openApprovals();
      } else if (item.kind === 'theme') {
        $('#theme-toggle')?.click();
      } else if (item.kind === 'sources') {
        api('/api/sources/check', { method: 'POST' })
          .then((data) => {
            const live = (data.sources || []).filter((s) => s.status === 'live').length;
            toast(`${live} of ${(data.sources || []).length} sources responded.`, { tone: live ? 'success' : 'error' });
          })
          .catch((error) => toast(error.message, { tone: 'error' }));
      }
    }

    $('#command-trigger')?.addEventListener('click', open);
    $$('[data-close-command]').forEach((el) => el.addEventListener('click', close));

    input.addEventListener('input', () => {
      index = 0;
      render();
    });

    document.addEventListener('keydown', (event) => {
      const isMac = navigator.platform.toLowerCase().includes('mac');
      if ((isMac ? event.metaKey : event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        palette.hidden ? open() : close();
        return;
      }
      if (palette.hidden) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
      } else if (event.key === 'ArrowDown') {
        event.preventDefault();
        index = clamp(index + 1, 0, results.length - 1);
        render();
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        index = clamp(index - 1, 0, results.length - 1);
        render();
      } else if (event.key === 'Enter') {
        event.preventDefault();
        activate(index);
      }
    });

    window.JP.openCommand = open;
  }

  // --- approvals drawer ---------------------------------------------------
  let approvalsState = null;

  function ensureApprovalsDrawer() {
    let backdrop = $('#approvals-backdrop');
    let drawer = $('#approvals-drawer');
    if (backdrop && drawer) return { backdrop, drawer };

    backdrop = document.createElement('div');
    backdrop.className = 'drawer-backdrop';
    backdrop.id = 'approvals-backdrop';
    drawer = document.createElement('aside');
    drawer.className = 'drawer';
    drawer.id = 'approvals-drawer';
    drawer.setAttribute('aria-label', 'Approvals');
    drawer.innerHTML = `
      <div class="drawer__head">
        <div class="drawer__head-main">
          <h2>Approvals</h2>
          <p>External actions and profile changes wait here until you decide.</p>
        </div>
        <button class="icon-btn" type="button" data-close-approvals aria-label="Close">${icon('x', 18)}</button>
      </div>
      <div class="drawer__body" id="approvals-body">
        <div class="inspector__empty">Loading approvals…</div>
      </div>
      <div class="drawer__foot">
        <button class="btn btn--secondary btn--sm" type="button" id="approvals-refresh">${icon('refresh', 14)} Refresh</button>
        <a class="btn btn--ghost btn--sm" href="/runs">${icon('activity', 14)} View traces</a>
      </div>`;
    document.body.appendChild(backdrop);
    document.body.appendChild(drawer);
    backdrop.addEventListener('click', closeApprovals);
    $('[data-close-approvals]', drawer).addEventListener('click', closeApprovals);
    $('#approvals-refresh', drawer).addEventListener('click', () => loadApprovals(true));
    return { backdrop, drawer };
  }

  function openApprovals() {
    const { backdrop, drawer } = ensureApprovalsDrawer();
    backdrop.classList.add('is-open');
    drawer.classList.add('is-open');
    document.body.style.overflow = 'hidden';
    loadApprovals(false);
  }

  function closeApprovals() {
    const backdrop = $('#approvals-backdrop');
    const drawer = $('#approvals-drawer');
    if (backdrop) backdrop.classList.remove('is-open');
    if (drawer) drawer.classList.remove('is-open');
    document.body.style.removeProperty('overflow');
  }

  async function loadApprovals(force) {
    if (approvalsState && !force) {
      renderApprovals(approvalsState);
      return;
    }
    const body = $('#approvals-body');
    if (body) body.innerHTML = '<div class="inspector__empty">Loading approvals…</div>';
    try {
      const data = await api('/api/approvals');
      approvalsState = data;
      updateApprovalBadge(data.pending);
      renderApprovals(data);
    } catch (error) {
      if (body) body.innerHTML = `<div class="inspector__empty">${escapeHtml(error.message)}</div>`;
    }
  }

  function approvalSummary(approval) {
    const args = approval.args || {};
    const pretty = Object.entries(args)
      .filter(([, value]) => value !== '' && value !== null && value !== undefined)
      .slice(0, 4)
      .map(([key, value]) => `${key}: ${typeof value === 'object' ? JSON.stringify(value) : value}`)
      .join(' · ');
    return pretty || 'No arguments';
  }

  function renderApprovals(data) {
    const body = $('#approvals-body');
    if (!body) return;
    const approvals = data.approvals || [];
    const pending = approvals.filter((a) => a.status === 'pending');
    const decided = approvals.filter((a) => a.status !== 'pending').slice(0, 8);
    const card = (approval, isPending) => `
      <div class="approval-card" data-approval="${escapeAttr(approval.id)}">
        <div class="approval-card__head">${icon(isPending ? 'shield' : 'check', 14)}
          <span>${escapeHtml(approval.tool)}</span>
          ${isPending ? '' : `<span class="badge badge--${approval.status === 'approved' ? 'success' : 'neutral'}" style="margin-left:auto">${escapeHtml(approval.status)}</span>`}
        </div>
        <p>${escapeHtml(approval.reason || 'This tool needs your explicit approval before it runs.')}</p>
        <div class="approval-card__tool">${escapeHtml(approvalSummary(approval))}</div>
        ${isPending ? `<div class="approval-card__actions">
          <button class="btn btn--success btn--xs" type="button" data-decide="approve">${icon('check', 12)} Approve</button>
          <button class="btn btn--danger btn--xs" type="button" data-decide="reject">${icon('x', 12)} Reject</button>
        </div>` : ''}
      </div>`;

    body.innerHTML =
      `<div class="inspector-section">
        <div class="inspector-section__head"><span>Pending · ${pending.length}</span>${pending.length ? '<span class="badge badge--warning">Needs action</span>' : ''}</div>
        ${pending.length ? pending.map((a) => card(a, true)).join('') : '<div class="inspector__empty">Nothing waiting. The agent will stop here before any external action.</div>'}
      </div>` +
      (decided.length
        ? `<div class="inspector-section">
            <div class="inspector-section__head"><span>Recent decisions</span></div>
            ${decided.map((a) => card(a, false)).join('')}
          </div>`
        : '');

    $$('[data-decide]', body).forEach((button) => {
      button.addEventListener('click', async () => {
        const wrap = button.closest('[data-approval]');
        const id = wrap.dataset.approval;
        const approve = button.dataset.decide === 'approve';
        $$('button', wrap).forEach((b) => (b.disabled = true));
        try {
          const result = await api(`/api/approvals/${encodeURIComponent(id)}/decide`, {
            method: 'POST',
            body: { approve },
          });
          const execution = result.execution;
          if (approve) {
            if (execution && execution.ok === false) {
              toast(execution.error || 'The tool ran but reported an error.', { tone: 'error', title: 'Approved, execution failed' });
            } else {
              toast('Approved and executed.', { tone: 'success', title: 'Approval recorded' });
            }
          } else {
            toast('Rejected. Nothing was executed.', { tone: 'info', title: 'Approval rejected' });
          }
          approvalsState = null;
          await loadApprovals(true);
          document.dispatchEvent(new CustomEvent('jobpilot:approvals-decided', { detail: { approve, id } }));
        } catch (error) {
          toast(error.message, { tone: 'error' });
          $$('button', wrap).forEach((b) => (b.disabled = false));
        }
      });
    });
  }

  function updateApprovalBadge(count) {
    const badge = $('#approval-count');
    if (!badge) return;
    badge.textContent = String(count || 0);
    badge.classList.toggle('is-hidden', !count);
    window.JOBPILOT.pendingApprovals = count || 0;
  }

  async function refreshApprovalCount() {
    try {
      const data = await api('/api/approvals');
      approvalsState = data;
      updateApprovalBadge(data.pending);
    } catch (error) {
      /* non-blocking */
    }
  }

  function initApprovals() {
    $('#approvals-btn')?.addEventListener('click', openApprovals);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && $('#approvals-drawer')?.classList.contains('is-open')) closeApprovals();
    });
    if ((meta.pendingApprovals || 0) > 0) refreshApprovalCount();
    window.JP.openApprovals = openApprovals;
    window.JP.loadApprovals = loadApprovals;
    window.JP.updateApprovalBadge = updateApprovalBadge;
  }

  // --- init ---------------------------------------------------------------
  document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    initSidebar();
    initCommandPalette();
    initApprovals();
  });

  window.JP = Object.assign(window.JP || {}, {
    $,
    $$,
    api,
    ApiError,
    icon,
    escapeHtml,
    escapeAttr,
    initials,
    debounce,
    clamp,
    formatDate,
    timeAgo,
    scoreRing,
    statusTone,
    emptyState,
    skeletonCards,
    toast,
    modal,
    closeModal,
    confirm: confirmDialog,
    copyText,
    openApprovals,
    closeApprovals,
    refreshApprovalCount,
  });
})();
