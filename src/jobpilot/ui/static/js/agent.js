/* Agent console — chat, sessions, run inspector, approvals. */
(function () {
  'use strict';
  const JP = window.JP;

  const state = {
    sessionId: '',
    busy: false,
    mode: 'auto',
  };

  const el = (id) => document.getElementById(id);

  // --- message formatting -------------------------------------------------
  function formatMessage(text) {
    let html = JP.escapeHtml(text || '');
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/`([^`]+)`/g, '<code style="font-family:var(--font-mono);font-size:11.5px;background:var(--surface-2);padding:1px 4px;border-radius:4px">$1</code>');
    html = html.replace(
      /(https?:\/\/[^\s<]+)/g,
      '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>'
    );
    html = html.replace(/\n/g, '<br>');
    return html;
  }

  function nowTime() {
    return new Date().toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  }

  function scrollChat() {
    const body = el('chat-body');
    if (body) body.scrollTop = body.scrollHeight;
  }

  function hideWelcome() {
    const welcome = el('chat-welcome');
    if (welcome) welcome.remove();
  }

  function addMessage(role, text, options = {}) {
    hideWelcome();
    const body = el('chat-body');
    if (!body) return null;
    const node = document.createElement('div');
    node.className = `message message--${role}`;
    const name = role === 'user' ? 'You' : 'JobPilot';
    const avatar = role === 'user' ? JP.initials(window.JOBPILOT.profileName || 'You') : 'JP';
    node.innerHTML = `
      <span class="message__avatar">${JP.escapeHtml(avatar)}</span>
      <div class="message__body">
        <span class="message__name">${JP.escapeHtml(name)}</span>
        <div class="bubble">${role === 'user' ? JP.escapeHtml(text) : formatMessage(text)}</div>
        <span class="message__time">${JP.escapeHtml(options.time || nowTime())}</span>
      </div>`;
    body.appendChild(node);
    scrollChat();
    return node;
  }

  function addTyping() {
    hideWelcome();
    const body = el('chat-body');
    if (!body) return null;
    const node = document.createElement('div');
    node.className = 'message';
    node.id = 'typing-indicator';
    node.innerHTML = `
      <span class="message__avatar">JP</span>
      <div class="message__body">
        <span class="message__name">JobPilot</span>
        <div class="bubble"><span class="typing"><span></span><span></span><span></span></span></div>
      </div>`;
    body.appendChild(node);
    scrollChat();
    return node;
  }

  // --- sessions -----------------------------------------------------------
  async function loadSessions(selectId) {
    const select = el('session-select');
    if (!select) return;
    try {
      const data = await JP.api('/api/sessions');
      const sessions = data.sessions || [];
      select.innerHTML = sessions.length
        ? sessions
            .map(
              (session) =>
                `<option value="${JP.escapeAttr(session.id)}">${JP.escapeHtml(session.title || 'Untitled')}</option>`
            )
            .join('')
        : '<option value="">New session</option>';
      if (selectId) {
        select.value = selectId;
        state.sessionId = selectId;
      } else if (sessions.length && !state.sessionId) {
        state.sessionId = sessions[0].id;
        select.value = state.sessionId;
      }
      return sessions;
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
      return [];
    }
  }

  async function openSession(sessionId) {
    state.sessionId = sessionId;
    const body = el('chat-body');
    if (!body) return;
    body.innerHTML = '<div class="chat-day">History</div>';
    if (!sessionId) {
      body.innerHTML += welcomeMarkup();
      bindSuggestions();
      return;
    }
    try {
      const data = await JP.api(`/api/sessions/${encodeURIComponent(sessionId)}`);
      const messages = data.messages || [];
      if (!messages.length) {
        body.innerHTML += welcomeMarkup();
        bindSuggestions();
        return;
      }
      messages
        .filter((message) => message.role === 'user' || message.role === 'assistant')
        .forEach((message) => {
          addMessage(message.role, message.content, { time: new Date(message.created_at).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }) });
        });
      const lastRun = messages[messages.length - 1].run_id;
      if (lastRun) {
        loadRun(lastRun);
      } else {
        resetInspector();
      }
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
    }
  }

  function welcomeMarkup() {
    return `
      <div class="chat-welcome" id="chat-welcome">
        <div class="chat-welcome__orb">${JP.icon('sparkles', 26)}</div>
        <h3>What should we chase today?</h3>
        <p>Ask for roles, a tailored CV, a skill-gap review, or a follow-up plan. Every action is traceable and verified against your profile.</p>
        <div class="suggestions">
          <button class="suggestion" type="button" data-prompt="Find remote Python AI engineer jobs and rank the best matches.">Find remote AI roles</button>
          <button class="suggestion" type="button" data-prompt="Tailor my CV to the best matching job and verify every claim.">Tailor my CV</button>
          <button class="suggestion" type="button" data-prompt="What skills am I missing for senior AI engineer roles?">Review my skill gap</button>
          <button class="suggestion" type="button" data-prompt="Which applications need a follow-up this week?">Check follow-ups</button>
        </div>
      </div>`;
  }

  function bindSuggestions() {
    JP.$$('.suggestion[data-prompt]').forEach((button) => {
      button.addEventListener('click', () => {
        const input = el('composer-input');
        if (input) {
          input.value = button.dataset.prompt;
          autoGrow();
          send();
        }
      });
    });
  }

  async function newSession() {
    try {
      const data = await JP.api('/api/sessions', { method: 'POST', body: { title: 'New session' } });
      state.sessionId = data.session.id;
      await loadSessions(state.sessionId);
      await openSession(state.sessionId);
      resetInspector();
      el('composer-input')?.focus();
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
    }
  }

  // --- inspector ----------------------------------------------------------
  function resetInspector() {
    const body = el('inspector-body');
    const status = el('inspector-status');
    if (status) {
      status.textContent = 'idle';
      status.className = 'badge badge--neutral';
    }
    if (body) {
      body.innerHTML = `<div class="inspector__empty">${JP.icon('terminal', 22)}<br><br>No run yet. Send a message and the execution plan appears here — including any approval gates.</div>`;
    }
  }

  function stepItem(step) {
    const ok = step.ok !== false;
    return `<div class="step-item ${ok ? 'is-ok' : 'is-fail'}">
      <span class="step-item__rail">${ok ? JP.icon('check', 10) : JP.icon('x', 10)}</span>
      <div class="step-item__main">
        <span class="step-item__tool">${JP.escapeHtml(step.tool || 'reasoning')}</span>
        <span class="step-item__obs">${JP.escapeHtml(step.observation || step.thought || '')}</span>
        <span class="step-item__meta">
          <span>step ${step.index}</span>
          ${step.latency_ms ? `<span>·</span><span>${Math.round(step.latency_ms)}ms</span>` : ''}
        </span>
      </div>
    </div>`;
  }

  function artifactCard(iconName, title, subtitle, href) {
    const tag = href ? 'a' : 'div';
    return `<${tag} class="artifact-card" ${href ? `href="${JP.escapeAttr(href)}"` : ''}>
      ${JP.icon(iconName, 16)}
      <span class="artifact-card__main"><strong>${JP.escapeHtml(title)}</strong><small>${JP.escapeHtml(subtitle)}</small></span>
      ${href ? JP.icon('chevron-right', 13) : ''}
    </${tag}>`;
  }

  function artifactMarkup(artifacts) {
    const cards = [];
    if (!artifacts || !Object.keys(artifacts).length) return '<p class="tiny faint">No artifacts for this run.</p>';
    const cv = artifacts.cv;
    if (cv) {
      cards.push(artifactCard('file-text', 'Tailored CV', `${(cv.bullets || []).length} bullet${(cv.bullets || []).length === 1 ? '' : 's'} · ${cv.unsupported_count || 0} unsupported`, '/drafts'));
    }
    const letter = artifacts.cover_letter;
    if (letter) {
      cards.push(artifactCard('send', 'Cover letter', `${(letter.body || '').length} characters · ${letter.unsupported_count || 0} unsupported`, '/drafts'));
    }
    const application = artifacts.application;
    if (application) {
      cards.push(artifactCard('kanban', 'Application tracked', `status: ${application.status || 'applied'}`, '/tracker'));
    }
    const memory = artifacts.memory;
    if (memory) {
      cards.push(artifactCard('brain', 'Memory proposal', memory.content || 'Saved fact', '/memory'));
    }
    const gap = artifacts.skill_gap;
    if (gap) {
      cards.push(
        artifactCard('chart', 'Skill gap', `${(gap.missing || []).length} missing · ${Math.round((gap.coverage || 0) * 100)}% coverage`, '/profile')
      );
    }
    (artifacts.jobs || artifacts.ranking || []).slice(0, 4).forEach((job) => {
      cards.push(
        artifactCard('briefcase', job.title || 'Role', `${job.company || 'Unknown'} · match ${Math.round(job.score || 0)}`, `/jobs?job=${encodeURIComponent(job.id || '')}`)
      );
    });
    (artifacts.followups || []).slice(0, 3).forEach((item) => {
      cards.push(artifactCard('calendar', item.job ? item.job.title : 'Follow-up', `status: ${item.status || '—'}`, '/tracker'));
    });
    (artifacts.applications || []).slice(0, 3).forEach((item) => {
      cards.push(artifactCard('kanban', item.job ? item.job.title : 'Application', `status: ${item.status || '—'}`, '/tracker'));
    });
    (artifacts.memories || []).slice(0, 3).forEach((item) => {
      cards.push(artifactCard('brain', item.content || 'Memory', item.kind || 'semantic', '/memory'));
    });
    if (artifacts.sources && artifacts.sources.length) {
      cards.push(
        `<div class="artifact-card">${JP.icon('server', 16)}<span class="artifact-card__main"><strong>Sources</strong><small>${JP.escapeHtml(artifacts.sources.join(', '))}</small></span></div>`
      );
    }
    return cards.length ? cards.join('') : '<p class="tiny faint">No structured artifacts for this run.</p>';
  }

  function approvalMarkup(approvals) {
    if (!approvals || !approvals.length) return '';
    return `<div class="inspector-section">
      <div class="inspector-section__head"><span>Approvals · ${approvals.length}</span><span class="badge badge--warning">Needs action</span></div>
      ${approvals
        .map(
          (approval) => `<div class="approval-card" data-approval="${JP.escapeAttr(approval.id)}">
            <div class="approval-card__head">${JP.icon('shield', 14)} ${JP.escapeHtml(approval.tool)}</div>
            <p>${JP.escapeHtml(approval.reason || 'External action requires explicit approval.')}</p>
            <div class="approval-card__tool">${JP.escapeHtml(JSON.stringify(approval.args || {}))}</div>
            ${
              approval.status === 'pending'
                ? `<div class="approval-card__actions">
                    <button class="btn btn--success btn--xs" type="button" data-approve="${JP.escapeAttr(approval.id)}">${JP.icon('check', 12)} Approve</button>
                    <button class="btn btn--danger btn--xs" type="button" data-reject="${JP.escapeAttr(approval.id)}">${JP.icon('x', 12)} Reject</button>
                  </div>`
                : `<div class="approval-card__tool">status: ${JP.escapeHtml(approval.status)}</div>`
            }
          </div>`
        )
        .join('')}
    </div>`;
  }

  function renderInspector(run, reply) {
    const body = el('inspector-body');
    const statusBadge = el('inspector-status');
    if (!body) return;
    const steps = reply.steps || [];
    const approvals = reply.approvals || [];
    const pending = approvals.filter((approval) => approval.status === 'pending');
    if (statusBadge) {
      statusBadge.textContent = reply.status || run.status || 'completed';
      statusBadge.className = `badge badge--${JP.statusTone(reply.status || run.status)}`;
    }
    body.innerHTML = `
      <div class="inspector-section">
        <div class="inspector-section__head"><span>Run</span><span class="mono">${JP.escapeHtml((run.id || reply.run_id || '').slice(0, 16))}</span></div>
        <div class="run-meta-grid">
          <div class="run-meta"><span>Mode</span><strong>${JP.escapeHtml(reply.mode || run.mode || 'local')}</strong></div>
          <div class="run-meta"><span>Steps</span><strong>${steps.length}</strong></div>
          <div class="run-meta"><span>Tokens</span><strong>${reply.tokens || run.tokens || 0}</strong></div>
          <div class="run-meta"><span>Cost</span><strong>$${Number(reply.cost || run.cost || 0).toFixed(4)}</strong></div>
        </div>
      </div>
      <div class="inspector-section">
        <div class="inspector-section__head"><span>Execution plan</span><span>${steps.length} step${steps.length === 1 ? '' : 's'}</span></div>
        <div class="step-list">${steps.length ? steps.map(stepItem).join('') : '<p class="tiny faint">The planner answered directly without tool calls.</p>'}</div>
      </div>
      ${approvalMarkup(pending)}
      <div class="inspector-section">
        <div class="inspector-section__head"><span>Artifacts</span><span>${Object.keys(reply.artifacts || {}).length} key${Object.keys(reply.artifacts || {}).length === 1 ? '' : 's'}</span></div>
        <div class="stack-8">${artifactMarkup(reply.artifacts)}</div>
      </div>
      <div class="inspector-section">
        <div class="inspector-section__head"><span>Verification</span></div>
        <div class="quality-item" style="align-items:flex-start">
          <span style="color:var(--green)">${JP.icon('shield', 15)}</span>
          <div><strong>Guardrails active</strong><small>Injection scanning, claim verification and approval gates ran on this response.</small></div>
        </div>
      </div>`;

    JP.$$('[data-approve]', body).forEach((button) => {
      button.addEventListener('click', () => decide(button.dataset.approve, true, button));
    });
    JP.$$('[data-reject]', body).forEach((button) => {
      button.addEventListener('click', () => decide(button.dataset.reject, false, button));
    });
  }

  async function decide(approvalId, approve, button) {
    const card = button.closest('[data-approval]');
    if (card) JP.$$('button', card).forEach((b) => (b.disabled = true));
    try {
      const data = await JP.api(`/api/approvals/${encodeURIComponent(approvalId)}/decide`, {
        method: 'POST',
        body: { approve },
      });
      if (approve) {
        if (data.execution && data.execution.ok === false) {
          JP.toast(data.execution.error || 'Tool execution failed after approval.', { tone: 'error' });
        } else {
          JP.toast('Approved and executed. Check the trace for details.', { title: 'Approval recorded' });
        }
      } else {
        JP.toast('Rejected. Nothing was executed.', { tone: 'info' });
      }
      if (card) {
        const actions = card.querySelector('.approval-card__actions');
        if (actions) actions.remove();
      }
      JP.refreshApprovalCount();
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
      if (card) JP.$$('button', card).forEach((b) => (b.disabled = false));
    }
  }

  async function loadRun(runId) {
    if (!runId) return;
    try {
      const data = await JP.api(`/api/runs/${encodeURIComponent(runId)}`);
      const run = data.run || {};
      const trace = data.trace || [];
      // Rebuild a light-weight reply shape from the stored run for the inspector.
      const toolCalls = trace.filter((event) => event.type === 'tool_call');
      const results = trace.filter((event) => event.type === 'tool_result');
      const steps = toolCalls.map((event, index) => {
        const result = results[index] || {};
        return {
          index: index + 1,
          tool: event.data.name || 'tool',
          observation: result.data && result.data.result ? String(result.data.result).slice(0, 240) : 'recorded',
          ok: result.data ? result.data.ok !== false : true,
          latency_ms: result.data ? result.data.latency_ms : 0,
          thought: '',
        };
      });
      const finished = trace.find((event) => event.type === 'run_finished');
      const body = el('inspector-body');
      const statusBadge = el('inspector-status');
      if (statusBadge) {
        statusBadge.textContent = run.status || 'completed';
        statusBadge.className = `badge badge--${JP.statusTone(run.status)}`;
      }
      if (body) {
        body.innerHTML = `
          <div class="inspector-section">
            <div class="inspector-section__head"><span>Archived run</span><span class="mono">${JP.escapeHtml((run.id || '').slice(0, 16))}</span></div>
            <div class="run-meta-grid">
              <div class="run-meta"><span>Mode</span><strong>${JP.escapeHtml(run.mode || 'local')}</strong></div>
              <div class="run-meta"><span>Status</span><strong>${JP.escapeHtml(run.status || '—')}</strong></div>
              <div class="run-meta"><span>Tokens</span><strong>${run.tokens || 0}</strong></div>
              <div class="run-meta"><span>Cost</span><strong>$${Number(run.cost || 0).toFixed(4)}</strong></div>
            </div>
          </div>
          <div class="inspector-section">
            <div class="inspector-section__head"><span>Goal</span></div>
            <p class="small muted">${JP.escapeHtml(run.goal || '')}</p>
          </div>
          <div class="inspector-section">
            <div class="inspector-section__head"><span>Tool calls from trace</span><span>${steps.length}</span></div>
            <div class="step-list">${steps.length ? steps.map(stepItem).join('') : '<p class="tiny faint">No tool calls recorded for this run.</p>'}</div>
          </div>
          ${
            finished
              ? `<div class="inspector-section"><div class="inspector-section__head"><span>Outcome</span></div><p class="small muted">${JP.escapeHtml(finished.data.summary || '')}</p></div>`
              : ''
          }`;
      }
    } catch (error) {
      /* archived run is optional context */
    }
  }

  // --- sending ------------------------------------------------------------
  function autoGrow() {
    const input = el('composer-input');
    if (!input) return;
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 150)}px`;
  }

  // The heartbeat and elapsed clock reflect a real in-flight request; they
  // never animate while the agent is idle.
  let runClock = null;
  let runStarted = 0;

  function startRunClock() {
    const dot = el('composer-dot');
    const elapsed = el('composer-elapsed');
    const badge = el('inspector-status');
    if (dot) dot.classList.add('is-live');
    if (elapsed) elapsed.hidden = false;
    if (badge) {
      badge.textContent = 'running';
      badge.className = 'badge badge--cyan';
    }
    runStarted = performance.now();
    clearInterval(runClock);
    runClock = setInterval(() => {
      const seconds = (performance.now() - runStarted) / 1000;
      if (elapsed) elapsed.textContent = `${seconds.toFixed(1)}s`;
    }, 100);
  }

  function stopRunClock() {
    clearInterval(runClock);
    runClock = null;
    const dot = el('composer-dot');
    const elapsed = el('composer-elapsed');
    const badge = el('inspector-status');
    if (dot) dot.classList.remove('is-live');
    if (elapsed) {
      elapsed.hidden = true;
      elapsed.textContent = '0.0s';
    }
    if (badge && badge.textContent === 'running') {
      badge.textContent = 'idle';
      badge.className = 'badge badge--neutral';
    }
  }

  async function send() {
    if (state.busy) return;
    const input = el('composer-input');
    const message = (input?.value || '').trim();
    if (!message) return;
    state.busy = true;
    input.value = '';
    autoGrow();
    addMessage('user', message);
    const typing = addTyping();
    const sendButton = el('composer-send');
    if (sendButton) {
      sendButton.disabled = true;
      sendButton.classList.add('is-loading');
    }
    const statusText = el('composer-status');
    if (statusText) statusText.textContent = 'Planning and executing tools…';
    startRunClock();
    try {
      const data = await JP.api('/api/agent/run', {
        method: 'POST',
        body: { message, session_id: state.sessionId, mode: state.mode },
      });
      const reply = data.reply || {};
      typing?.remove();
      if (reply.session_id) {
        state.sessionId = reply.session_id;
      }
      addMessage('assistant', reply.message || 'Done.');
      if (reply.status === 'awaiting_approval' || (reply.approvals || []).length) {
        JP.toast('The agent paused for approval before continuing.', { tone: 'info', title: 'Approval required' });
      }
      renderInspector({ id: reply.run_id, mode: reply.mode, tokens: reply.tokens, cost: reply.cost }, reply);
      const sessions = await loadSessions(state.sessionId);
      if (sessions && sessions.length) {
        const select = el('session-select');
        if (select) select.value = state.sessionId;
      }
      JP.refreshApprovalCount();
    } catch (error) {
      typing?.remove();
      addMessage('assistant', `I could not complete that run: ${error.message}\n\nTry switching the mode to local, or check Settings → system status.`);
      JP.toast(error.message, { tone: 'error', title: 'Agent run failed' });
    } finally {
      state.busy = false;
      stopRunClock();
      if (sendButton) {
        sendButton.disabled = false;
        sendButton.classList.remove('is-loading');
      }
      if (statusText) {
        statusText.textContent = window.JOBPILOT.llmEnabled ? 'LLM gateway ready' : 'Deterministic local planner';
      }
      input?.focus();
    }
  }

  // --- boot ---------------------------------------------------------------
  function boot() {
    const input = el('composer-input');
    const form = el('composer');
    form?.addEventListener('submit', (event) => {
      event.preventDefault();
      send();
    });
    input?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        send();
      }
    });
    input?.addEventListener('input', autoGrow);

    el('new-session')?.addEventListener('click', newSession);
    el('session-select')?.addEventListener('change', (event) => openSession(event.target.value));
    el('agent-mode')?.addEventListener('change', (event) => {
      state.mode = event.target.value;
      JP.toast(`Agent mode set to ${state.mode}.`, { tone: 'info', duration: 2200 });
    });

    bindSuggestions();
    loadSessions().then(() => {
      if (state.sessionId) openSession(state.sessionId);
    });

    let prompt = '';
    try {
      prompt = sessionStorage.getItem('jobpilot:agent-prompt') || '';
      if (prompt) sessionStorage.removeItem('jobpilot:agent-prompt');
    } catch (error) {
      /* ignore */
    }
    if (prompt && input) {
      input.value = prompt;
      autoGrow();
      setTimeout(send, 320);
    } else {
      input?.focus();
    }
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
