/* Drafts — tailored CVs and cover letters with verification state. */
(function () {
  'use strict';
  const JP = window.JP;

  const state = { cvs: [], letters: [], tab: 'cvs' };
  const el = (id) => document.getElementById(id);

  function jobOf(draft) {
    return draft.job || { title: 'Untitled role', company: 'Unknown', id: draft.job_id };
  }

  function cvDownloads(cv) {
    const files = cv.files || {};
    return ['docx', 'pdf']
      .filter((fmt) => files[fmt])
      .map((fmt) => ({ fmt, url: `/api/tailor/cv/${encodeURIComponent(cv.id)}/download/${fmt}` }));
  }

  function downloadButtons(cv, size) {
    const cls = size === 'xs' ? 'btn btn--secondary btn--xs' : 'btn btn--secondary';
    return cvDownloads(cv)
      .map(
        (item) =>
          `<a class="${cls}" href="${item.url}" download>${JP.icon('download', size === 'xs' ? 12 : 14)} ${item.fmt.toUpperCase()}</a>`
      )
      .join('');
  }

  function changesMarkup(changes) {
    if (!changes || !changes.length) return '<p class="tiny faint">No change summary recorded.</p>';
    return `<div class="quality-list">${changes
      .map(
        (change) => `<div class="quality-item">
          <span style="color:var(--green)">${JP.icon('check', 14)}</span>
          <div><strong>${JP.escapeHtml(change.label || change.field || 'Update')}</strong><small>${JP.escapeHtml(change.detail || '')}</small></div>
        </div>`
      )
      .join('')}</div>`;
  }

  function cvText(cv) {
    const lines = [];
    const job = jobOf(cv);
    lines.push(`Tailored CV — ${job.title} @ ${job.company}`, '');
    if (cv.headline) lines.push(cv.headline, '');
    if (cv.summary) lines.push(cv.summary, '');
    if (cv.skills && cv.skills.length) lines.push(`Skills: ${cv.skills.join(', ')}`, '');
    (cv.bullets || []).forEach((bullet) => lines.push(`• ${bullet.text}`));
    lines.push('', `Verification: ${cv.verified ? 'all claims supported' : `${cv.unsupported_count} unsupported claim(s)`}`);
    return lines.join('\n');
  }

  function letterText(letter) {
    const job = jobOf(letter);
    return `Cover letter — ${job.title} @ ${job.company}\n\n${letter.body || ''}\n\nVerification: ${letter.verified ? 'all claims supported' : `${letter.unsupported_count} unsupported claim(s)`}`;
  }

  function downloadText(name, content) {
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = name;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
    JP.toast('Download started.', { tone: 'info', duration: 2200 });
  }

  function cvCard(cv) {
    const job = jobOf(cv);
    return `<article class="draft-card" data-cv="${JP.escapeAttr(cv.id)}">
      <div class="draft-card__head">
        <span class="stat-card__icon">${JP.icon('file-text', 17)}</span>
        <div class="draft-card__head-main">
          <h3>${JP.escapeHtml(job.title)}</h3>
          <p>${JP.escapeHtml(job.company)} · ${JP.escapeHtml(JP.timeAgo(cv.created_at))}</p>
        </div>
        <span class="badge badge--${cv.verified ? 'success' : 'warning'}">${cv.verified ? 'Verified' : `${cv.unsupported_count} flagged`}</span>
      </div>
      <p class="draft-preview">${JP.escapeHtml(cv.summary || cv.headline || 'No summary generated.')}</p>
      <div class="chip-list">
        ${(cv.skills || []).slice(0, 5).map((skill) => `<span class="chip chip--accent">${JP.escapeHtml(skill)}</span>`).join('')}
        ${(cv.skills || []).length > 5 ? `<span class="chip chip--muted">+${cv.skills.length - 5}</span>` : ''}
      </div>
      <div class="draft-card__foot">
        <span class="tiny faint">${(cv.bullets || []).length} bullet${(cv.bullets || []).length === 1 ? '' : 's'} · ${cv.method || 'template'}</span>
        <span class="spacer"></span>
        ${downloadButtons(cv, 'xs')}
        <button class="btn btn--secondary btn--xs" type="button" data-open-cv="${JP.escapeAttr(cv.id)}">${JP.icon('search', 12)} View</button>
        <button class="btn btn--ghost btn--xs" type="button" data-copy-cv="${JP.escapeAttr(cv.id)}">${JP.icon('copy', 12)} Copy</button>
      </div>
    </article>`;
  }

  function letterCard(letter) {
    const job = jobOf(letter);
    return `<article class="draft-card" data-letter="${JP.escapeAttr(letter.id)}">
      <div class="draft-card__head">
        <span class="stat-card__icon stat-card__icon--cyan">${JP.icon('send', 17)}</span>
        <div class="draft-card__head-main">
          <h3>${JP.escapeHtml(job.title)}</h3>
          <p>${JP.escapeHtml(job.company)} · ${JP.escapeHtml(JP.timeAgo(letter.created_at))}</p>
        </div>
        <span class="badge badge--${letter.verified ? 'success' : 'warning'}">${letter.verified ? 'Verified' : `${letter.unsupported_count} flagged`}</span>
      </div>
      <p class="draft-preview">${JP.escapeHtml((letter.body || '').slice(0, 400))}</p>
      <div class="draft-card__foot">
        <span class="tiny faint">${(letter.body || '').split(/\s+/).filter(Boolean).length} words · ${(letter.checks || []).length} claim${(letter.checks || []).length === 1 ? '' : 's'} checked</span>
        <span class="spacer"></span>
        <button class="btn btn--secondary btn--xs" type="button" data-open-letter="${JP.escapeAttr(letter.id)}">${JP.icon('search', 12)} View</button>
        <button class="btn btn--ghost btn--xs" type="button" data-copy-letter="${JP.escapeAttr(letter.id)}">${JP.icon('copy', 12)} Copy</button>
      </div>
    </article>`;
  }

  function render() {
    const grid = el('draft-grid');
    if (!grid) return;
    const items = state.tab === 'cvs' ? state.cvs : state.letters;
    if (!items.length) {
      grid.innerHTML = JP.emptyState(
        state.tab === 'cvs' ? 'file-text' : 'send',
        state.tab === 'cvs' ? 'No tailored CVs yet' : 'No cover letters yet',
        'Open a job, then choose Tailor CV or Cover letter. Everything is generated from your saved profile and checked for unsupported claims.',
        '<a class="btn btn--primary btn--sm" href="/jobs">Find a job to tailor</a>'
      );
      return;
    }
    grid.innerHTML = state.tab === 'cvs' ? state.cvs.map(cvCard).join('') : state.letters.map(letterCard).join('');

    JP.$$('[data-open-cv]', grid).forEach((button) =>
      button.addEventListener('click', () => openCv(button.dataset.openCv))
    );
    JP.$$('[data-copy-cv]', grid).forEach((button) =>
      button.addEventListener('click', () => {
        const cv = state.cvs.find((item) => item.id === button.dataset.copyCv);
        if (cv) JP.copyText(cvText(cv));
      })
    );
    JP.$$('[data-open-letter]', grid).forEach((button) =>
      button.addEventListener('click', () => openLetter(button.dataset.openLetter))
    );
    JP.$$('[data-copy-letter]', grid).forEach((button) =>
      button.addEventListener('click', () => {
        const letter = state.letters.find((item) => item.id === button.dataset.copyLetter);
        if (letter) JP.copyText(letterText(letter));
      })
    );
    JP.$$('.draft-card', grid).forEach((card) => {
      card.addEventListener('click', (event) => {
        if (event.target.closest('button, a')) return;
        if (card.dataset.cv) openCv(card.dataset.cv);
        if (card.dataset.letter) openLetter(card.dataset.letter);
      });
    });
  }

  function checksMarkup(checks) {
    if (!checks || !checks.length) return '<p class="tiny faint">No claim checks recorded.</p>';
    return `<div class="quality-list">${checks
      .map(
        (check) => `<div class="quality-item">
          <span style="color:var(--${check.supported ? 'green' : 'red'})">${JP.icon(check.supported ? 'check' : 'alert', 14)}</span>
          <div><strong>${JP.escapeHtml(check.claim)}</strong><small>${JP.escapeHtml(check.evidence || check.reason || 'No evidence quoted')}</small></div>
        </div>`
      )
      .join('')}</div>`;
  }

  function openCv(cvId) {
    const cv = state.cvs.find((item) => item.id === cvId);
    if (!cv) return;
    const job = jobOf(cv);
    JP.modal({
      title: job.title,
      subtitle: `${job.company} · generated ${JP.formatDate(cv.created_at)}`,
      wide: true,
      body: `
        <div class="chip-list">
          <span class="badge badge--${cv.verified ? 'success' : 'warning'}">${cv.verified ? 'All claims supported' : `${cv.unsupported_count} unsupported`}</span>
          <span class="badge badge--neutral">${(cv.bullets || []).length} bullet${(cv.bullets || []).length === 1 ? '' : 's'}</span>
          <span class="badge badge--neutral">${cv.method || 'template'}</span>
        </div>
        <div><div class="section-label">Target job</div><p class="strong mt-8">${JP.escapeHtml(job.title)} · ${JP.escapeHtml(job.company || 'Unknown')}</p></div>
        <div><div class="section-label">What changed</div><div class="mt-8">${changesMarkup(cv.changes)}</div></div>
        <div><div class="section-label">Headline</div><p class="strong mt-8">${JP.escapeHtml(cv.headline || '')}</p></div>
        <div><div class="section-label">Summary</div><p class="small muted mt-8" style="line-height:1.65">${JP.escapeHtml(cv.summary || '')}</p></div>
        <div><div class="section-label">Skills</div><div class="chip-list mt-8">${(cv.skills || []).map((s) => `<span class="chip chip--accent">${JP.escapeHtml(s)}</span>`).join('') || '<span class="chip chip--muted">None</span>'}</div></div>
        <div><div class="section-label">Experience bullets</div><div class="quality-list mt-8">${(cv.bullets || [])
          .map(
            (bullet) => `<div class="quality-item">
              <span style="color:var(--${bullet.supported ? 'green' : 'red'})">${JP.icon(bullet.supported ? 'check' : 'alert', 14)}</span>
              <div><strong>${JP.escapeHtml(bullet.text)}</strong><small>${JP.escapeHtml(bullet.evidence || bullet.source_bullet || 'No evidence')}</small></div>
            </div>`
          )
          .join('') || '<p class="tiny faint">No bullets generated.</p>'}</div></div>
        <div><div class="section-label">Claim checks</div><div class="mt-8">${checksMarkup(cv.checks)}</div></div>`,
      footer: `<button class="btn btn--ghost" type="button" data-modal-close>Close</button>
        ${downloadButtons(cv) || '<span class="badge badge--warning">No downloadable file generated</span>'}
        <button class="btn btn--primary" type="button" id="copy-cv">${JP.icon('copy', 14)} Copy text</button>`,
      onMount(root) {
        root.querySelector('#copy-cv').addEventListener('click', () => JP.copyText(cvText(cv)));
      },
    });
  }

  function openLetter(letterId) {
    const letter = state.letters.find((item) => item.id === letterId);
    if (!letter) return;
    const job = jobOf(letter);
    JP.modal({
      title: job.title,
      subtitle: `${job.company} · generated ${JP.formatDate(letter.created_at)}`,
      wide: true,
      body: `
        <div class="chip-list">
          <span class="badge badge--${letter.verified ? 'success' : 'warning'}">${letter.verified ? 'Verified' : `${letter.unsupported_count} unsupported`}</span>
          <span class="badge badge--neutral">${(letter.checks || []).length} claims checked</span>
          <span class="badge badge--neutral">${letter.method || 'template'}</span>
        </div>
        <div class="job-description" style="max-height:46vh">${JP.escapeHtml(letter.body || '')}</div>
        <div><div class="section-label">Claim checks</div><div class="mt-8">${checksMarkup(letter.checks)}</div></div>`,
      footer: `<button class="btn btn--ghost" type="button" data-modal-close>Close</button>
        <button class="btn btn--secondary" type="button" id="dl-letter">${JP.icon('download', 14)} Download</button>
        <button class="btn btn--primary" type="button" id="copy-letter">${JP.icon('copy', 14)} Copy letter</button>`,
      onMount(root) {
        root.querySelector('#copy-letter').addEventListener('click', () => JP.copyText(letterText(letter)));
        root.querySelector('#dl-letter').addEventListener('click', () => downloadText(`cover-letter-${job.company || 'job'}.txt`, letterText(letter)));
      },
    });
  }

  async function load() {
    try {
      const data = await JP.api('/api/drafts');
      state.cvs = data.cvs || [];
      state.letters = data.letters || [];
      render();
      const verified = state.cvs.filter((cv) => cv.verified).length + state.letters.filter((letter) => letter.verified).length;
      const badge = el('draft-verified');
      if (badge) badge.innerHTML = `${JP.icon('shield', 12)} ${verified} verified draft${verified === 1 ? '' : 's'}`;
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
    }
  }

  function boot() {
    el('drafts-refresh')?.addEventListener('click', () => load().then(() => JP.toast('Drafts refreshed.', { tone: 'info', duration: 2000 })));
    JP.$$('[data-tab]').forEach((tab) => {
      tab.addEventListener('click', () => {
        state.tab = tab.dataset.tab;
        JP.$$('[data-tab]').forEach((other) => {
          const active = other === tab;
          other.classList.toggle('is-active', active);
          other.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        render();
      });
    });
    load();
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
