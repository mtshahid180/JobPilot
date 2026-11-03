/* First-run setup — prefill, CV import, save. */
(function () {
  'use strict';
  const JP = window.JP;
  const el = (id) => document.getElementById(id);

  const splitList = (value) =>
    Array.from(
      new Set(
        String(value || '')
          .split(/[,\n]/)
          .map((item) => item.trim())
          .filter(Boolean)
      )
    );

  function fill(profile) {
    const set = (id, value) => {
      const node = el(id);
      if (node) node.value = value || '';
    };
    set('setup-name', profile.full_name === 'Your Name' ? '' : profile.full_name);
    set('setup-headline', profile.headline);
    set('setup-email', profile.email);
    set('setup-location', profile.location);
    set('setup-summary', profile.summary);
    set('setup-targets', (profile.target_roles || []).join(', '));
    set('setup-skills', (profile.skills || []).join(', '));
    if (el('setup-remote') && profile.remote_preference) {
      el('setup-remote').value = profile.remote_preference;
    }
  }

  function setTab(name) {
    document.querySelectorAll('[data-setup-tab]').forEach((button) => {
      const active = button.dataset.setupTab === name;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-selected', String(active));
    });
    const form = el('setup-form');
    const cv = el('setup-cv');
    const upload = el('setup-upload');
    if (form) form.hidden = name !== 'manual';
    if (cv) cv.hidden = name !== 'cv';
    if (upload) upload.hidden = name !== 'upload';
  }

  async function load() {
    try {
      const data = await JP.api('/api/profile');
      fill(data.profile || {});
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
    }
  }

  async function save(event) {
    event.preventDefault();
    const name = (el('setup-name')?.value || '').trim();
    if (!name || name === 'Your Name') {
      JP.toast('Add your real full name to continue.', { tone: 'error' });
      el('setup-name')?.focus();
      return;
    }
    const button = el('setup-save');
    if (button) {
      button.disabled = true;
      button.classList.add('is-loading');
    }
    try {
      await JP.api('/api/profile', {
        method: 'PUT',
        body: {
          full_name: name,
          headline: (el('setup-headline')?.value || '').trim(),
          email: (el('setup-email')?.value || '').trim(),
          location: (el('setup-location')?.value || '').trim(),
          summary: (el('setup-summary')?.value || '').trim(),
          target_roles: splitList(el('setup-targets')?.value),
          skills: splitList(el('setup-skills')?.value),
          remote_preference: el('setup-remote')?.value || 'any',
        },
      });
      JP.toast('Profile saved. Your dashboard is ready.', { title: 'You are set up' });
      window.setTimeout(() => window.location.assign('/'), 350);
    } catch (error) {
      JP.toast(error.message, { tone: 'error', title: 'Could not save profile' });
      if (button) {
        button.disabled = false;
        button.classList.remove('is-loading');
      }
    }
  }

  async function parseCv() {
    const text = (el('cv-text')?.value || '').trim();
    if (text.length < 40) {
      JP.toast('Paste at least 40 characters of CV text.', { tone: 'error' });
      return;
    }
    const button = el('setup-parse');
    if (button) {
      button.disabled = true;
      button.classList.add('is-loading');
    }
    try {
      const data = await JP.api('/api/profile/parse-cv', { method: 'POST', body: { text } });
      const fresh = await JP.api('/api/profile');
      fill(fresh.profile || data.profile || {});
      setTab('manual');
      JP.toast('CV parsed. Check every field before saving.', { title: 'CV imported' });
    } catch (error) {
      JP.toast(error.message, { tone: 'error', title: 'Could not parse CV' });
    } finally {
      if (button) {
        button.disabled = false;
        button.classList.remove('is-loading');
      }
    }
  }

  function boot() {
    document.querySelectorAll('[data-setup-tab]').forEach((button) => {
      button.addEventListener('click', () => setTab(button.dataset.setupTab));
    });
    el('setup-form')?.addEventListener('submit', save);
    el('setup-parse')?.addEventListener('click', parseCv);
    document.addEventListener('resume:applied', (event) => {
      const profile = (event.detail && event.detail.profile) || event.detail || {};
      if (profile && profile.full_name) fill(profile);
      setTab('manual');
      JP.toast('Resume fields were merged into your profile. Review them, then save to continue.', {
        tone: 'info',
        title: 'Resume applied',
      });
    });
    load();
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
