/* Resume upload, extraction review and apply — shared by setup and profile. */
(function () {
  'use strict';
  const JP = window.JP;
  const el = (id) => document.getElementById(id);

  const FIELD_META = {
    full_name: { label: 'Full name', type: 'text' },
    headline: { label: 'Headline', type: 'text' },
    email: { label: 'Email', type: 'text' },
    phone: { label: 'Phone', type: 'text' },
    location: { label: 'Location', type: 'text' },
    summary: { label: 'Summary', type: 'textarea' },
    skills: { label: 'Skills', type: 'list' },
    links: { label: 'Links', type: 'list' },
    experience: { label: 'Experience', type: 'readonly' },
    education: { label: 'Education', type: 'readonly' },
    projects: { label: 'Projects', type: 'readonly' },
    certifications: { label: 'Certifications', type: 'readonly' },
    achievements: { label: 'Achievements', type: 'readonly' },
  };

  const STATUS_TONE = {
    uploaded: 'neutral',
    extracting: 'accent',
    extracted: 'success',
    needs_review: 'warning',
    failed: 'danger',
  };

  let activeDraft = null;

  function splitList(value) {
    return Array.from(
      new Set(
        String(value || '')
          .split(/[,\n]/)
          .map((item) => item.trim())
          .filter(Boolean)
      )
    );
  }

  function setStatus(message, tone) {
    const node = el('resume-status');
    if (!node) return;
    node.innerHTML = tone ? `<span class="badge badge--${tone}">${JP.escapeHtml(message)}</span>` : JP.escapeHtml(message);
  }

  function setBusy(busy) {
    const button = el('resume-upload');
    if (button) {
      button.disabled = busy;
      button.classList.toggle('is-loading', busy);
    }
  }

  function describeValue(field, draft) {
    if (field === 'experience') {
      const count = (draft.experience || []).length;
      return `${count} role${count === 1 ? '' : 's'}`;
    }
    if (field === 'education') {
      const count = (draft.education || []).length;
      return `${count} entr${count === 1 ? 'y' : 'ies'}`;
    }
    if (field === 'projects' || field === 'certifications') {
      const items = draft[field] || [];
      const names = items.map((item) => item.name).filter(Boolean);
      return names.length ? names.join(', ') : '';
    }
    const value = draft[field];
    if (Array.isArray(value)) return value.join(', ');
    return value || '';
  }

  function renderReview(payload, changed, warnings) {
    const wrap = el('resume-review');
    if (!wrap) return;
    const draft = payload.profile_draft || (payload.extracted || {});
    const selected = new Set((changed || []).filter((field) => FIELD_META[field]));
    const rows = Object.entries(FIELD_META)
      .map(([field, meta]) => {
        const raw = describeValue(field, draft);
        const hasValue = Boolean(String(raw || '').trim()) && raw !== '0 roles' && raw !== '0 entries';
        const checked = selected.has(field) && hasValue;
        let control = '';
        if (meta.type === 'readonly') {
          control = `<span class="resume-field__readonly">${JP.escapeHtml(raw || 'not detected')}</span>`;
        } else if (meta.type === 'textarea') {
          control = `<textarea class="textarea" rows="3" data-resume-value="${field}">${JP.escapeHtml(String(draft[field] || ''))}</textarea>`;
        } else if (meta.type === 'list') {
          const value = Array.isArray(draft[field]) ? draft[field].join(', ') : String(draft[field] || '');
          control = `<input class="input" type="text" data-resume-value="${field}" value="${JP.escapeAttr(value)}">`;
        } else {
          control = `<input class="input" type="text" data-resume-value="${field}" value="${JP.escapeAttr(String(draft[field] || ''))}">`;
        }
        return `<div class="resume-field ${hasValue ? '' : 'is-empty'}">
          <label class="filter-check resume-field__check">
            <input type="checkbox" data-resume-field="${field}" ${checked ? 'checked' : ''} ${hasValue ? '' : 'disabled'}>
            <span>${JP.escapeHtml(meta.label)}</span>
          </label>
          ${control}
        </div>`;
      })
      .join('');

    wrap.hidden = false;
    wrap.innerHTML = `
      <div class="resume-review__head">
        <div>
          <strong>Review before applying</strong>
          <p class="field-hint">Nothing has been saved. Tick the fields you want to merge, edit any value, then apply.</p>
        </div>
        <div class="row-8">
          <button class="btn btn--ghost btn--xs" type="button" id="resume-select-all">Select detected</button>
          <button class="btn btn--ghost btn--xs" type="button" id="resume-clear-all">Clear</button>
        </div>
      </div>
      ${(warnings || []).length ? `<ul class="notes-list">${warnings.map((warning) => `<li>${JP.escapeHtml(warning)}</li>`).join('')}</ul>` : ''}
      <div class="resume-fields">${rows}</div>
      <div class="row-8 wrap">
        <button class="btn btn--primary" type="button" id="resume-apply">${JP.icon('check', 15)} Apply selected fields</button>
        <button class="btn btn--ghost" type="button" id="resume-discard">Discard draft</button>
      </div>`;

    el('resume-select-all')?.addEventListener('click', () => {
      JP.$$('[data-resume-field]', wrap).forEach((node) => {
        if (!node.disabled) node.checked = true;
      });
    });
    el('resume-clear-all')?.addEventListener('click', () => {
      JP.$$('[data-resume-field]', wrap).forEach((node) => {
        node.checked = false;
      });
    });
    el('resume-discard')?.addEventListener('click', () => {
      activeDraft = null;
      wrap.hidden = true;
      setStatus('Draft discarded. Nothing was applied.');
    });
    el('resume-apply')?.addEventListener('click', applyDraft);
  }

  function collectPatch() {
    const patch = {};
    JP.$$('[data-resume-value]').forEach((node) => {
      const field = node.dataset.resumeValue;
      if (!field) return;
      patch[field] = FIELD_META[field]?.type === 'list' ? splitList(node.value) : node.value.trim();
    });
    return patch;
  }

  async function applyDraft() {
    if (!activeDraft) return;
    const fields = JP.$$('[data-resume-field]')
      .filter((node) => node.checked && !node.disabled)
      .map((node) => node.dataset.resumeField);
    if (!fields.length) {
      JP.toast('Select at least one field to merge.', { tone: 'error' });
      return;
    }
    const button = el('resume-apply');
    if (button) {
      button.disabled = true;
      button.classList.add('is-loading');
    }
    try {
      const data = await JP.api(`/api/resumes/${encodeURIComponent(activeDraft.id)}/apply`, {
        method: 'POST',
        body: { fields, patch: collectPatch() },
      });
      activeDraft = null;
      el('resume-review').hidden = true;
      setStatus(`Applied ${fields.length} field${fields.length === 1 ? '' : 's'} to your profile.`, 'success');
      JP.toast('Resume fields merged into your profile.', { title: 'Profile updated' });
      document.dispatchEvent(new CustomEvent('resume:applied', { detail: data.profile || {} }));
      loadList();
    } catch (error) {
      JP.toast(error.message, { tone: 'error', title: 'Could not apply resume' });
    } finally {
      if (button) {
        button.disabled = false;
        button.classList.remove('is-loading');
      }
    }
  }

  async function upload() {
    const input = el('resume-file');
    const file = input?.files?.[0];
    if (!file) {
      JP.toast('Choose a PDF, DOCX, TXT or Markdown file first.', { tone: 'error' });
      return;
    }
    const form = new FormData();
    form.append('file', file);
    setBusy(true);
    setStatus('Extracting text and building a review draft…', 'accent');
    try {
      const response = await fetch('/api/resumes', {
        method: 'POST',
        body: form,
        headers: { Accept: 'application/json' },
      });
      let data = {};
      try {
        data = await response.json();
      } catch (error) {
        data = { ok: false, error: `Upload failed (${response.status})` };
      }
      if (!response.ok || data.ok === false) {
        const message = data.error || data.message || `Upload failed (${response.status})`;
        setStatus(message, 'danger');
        if (data.resume) renderReview(data.resume, [], data.warnings || [message]);
        JP.toast(message, { tone: 'error', title: 'Could not read resume' });
        loadList();
        return;
      }
      activeDraft = data.resume || {};
      setStatus('Review the extracted fields below. Nothing is saved yet.', 'warning');
      renderReview(data.resume || {}, data.changed_fields || [], data.warnings || []);
      loadList();
      JP.toast('Resume parsed. Review the fields before applying.', { tone: 'info', title: 'Extraction complete' });
    } catch (error) {
      setStatus(error.message, 'danger');
      JP.toast(error.message, { tone: 'error' });
    } finally {
      setBusy(false);
      if (input) input.value = '';
    }
  }

  async function setCurrent(resumeId) {
    try {
      await JP.api(`/api/resumes/${encodeURIComponent(resumeId)}/current`, { method: 'POST', body: {} });
      JP.toast('Marked as your current resume.', { tone: 'info' });
      loadList();
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
    }
  }

  async function remove(resumeId) {
    const ok = await JP.confirm({
      title: 'Delete this resume?',
      message: 'The stored file and its extracted draft are removed. Your saved profile is not changed.',
      confirmLabel: 'Delete resume',
      danger: true,
    });
    if (!ok) return;
    try {
      await JP.api(`/api/resumes/${encodeURIComponent(resumeId)}`, { method: 'DELETE' });
      JP.toast('Resume deleted.', { tone: 'info' });
      if (activeDraft && activeDraft.id === resumeId) {
        activeDraft = null;
        el('resume-review').hidden = true;
      }
      loadList();
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
    }
  }

  async function loadList() {
    const list = el('resume-list');
    if (!list) return;
    try {
      const data = await JP.api('/api/resumes');
      const resumes = data.resumes || [];
      if (!resumes.length) {
        list.innerHTML = '<p class="field-hint">No resume uploaded yet. A text-based PDF or DOCX works best.</p>';
        return;
      }
      list.innerHTML = resumes
        .map((resume) => {
          const tone = STATUS_TONE[resume.status] || 'neutral';
          return `<div class="resume-item">
            <span class="resume-item__icon">${JP.icon('file-text', 16)}</span>
            <div class="resume-item__main">
              <strong>${JP.escapeHtml(resume.filename)}</strong>
              <span class="field-hint">
                ${Math.max(1, Math.round((resume.size || 0) / 1024))} KB ·
                ${JP.escapeHtml(JP.timeAgo(resume.created_at))} ·
                ${resume.text_chars || 0} characters extracted
              </span>
              ${resume.error ? `<span class="field-hint resume-item__error">${JP.escapeHtml(resume.error)}</span>` : ''}
            </div>
            <span class="badge badge--${tone}">${JP.escapeHtml(resume.status.replace('_', ' '))}</span>
            ${resume.is_current ? '<span class="badge badge--success">Current</span>' : `<button class="btn btn--ghost btn--xs" type="button" data-resume-current="${JP.escapeAttr(resume.id)}">Set current</button>`}
            <button class="btn btn--ghost btn--xs" type="button" data-resume-delete="${JP.escapeAttr(resume.id)}" aria-label="Delete ${JP.escapeAttr(resume.filename)}">${JP.icon('trash', 13)}</button>
          </div>`;
        })
        .join('');
      JP.$$('[data-resume-current]', list).forEach((button) => {
        button.addEventListener('click', () => setCurrent(button.dataset.resumeCurrent));
      });
      JP.$$('[data-resume-delete]', list).forEach((button) => {
        button.addEventListener('click', () => remove(button.dataset.resumeDelete));
      });
    } catch (error) {
      list.innerHTML = `<p class="field-hint">Could not load stored resumes: ${JP.escapeHtml(error.message)}</p>`;
    }
  }

  function boot() {
    if (!el('resume-file') && !el('resume-list')) return;
    el('resume-upload')?.addEventListener('click', upload);
    el('resume-file')?.addEventListener('change', () => {
      const file = el('resume-file')?.files?.[0];
      if (file) setStatus(`${file.name} selected. Upload to extract.`);
    });
    loadList();
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
