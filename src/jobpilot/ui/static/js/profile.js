/* Profile editor — repeaters, skills, completeness, CV import. */
(function () {
  'use strict';
  const JP = window.JP;

  let skills = [];
  let dirty = false;
  let loading = false;

  const el = (id) => document.getElementById(id);
  const form = () => el('profile-form');

  // --- helpers ------------------------------------------------------------
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

  function setDirty(value) {
    dirty = value;
    const bar = el('sticky-save');
    if (bar) bar.classList.toggle('is-visible', dirty);
  }

  // --- skills -------------------------------------------------------------
  function renderSkills() {
    const wrap = el('skill-chips');
    const count = el('skill-count');
    if (count) count.textContent = `${skills.length} skill${skills.length === 1 ? '' : 's'}`;
    if (!wrap) return;
    wrap.innerHTML = skills.length
      ? skills
          .map(
            (skill) =>
              `<span class="chip chip--accent">${JP.escapeHtml(skill)}<button type="button" data-remove-skill="${JP.escapeAttr(skill)}" aria-label="Remove ${JP.escapeAttr(skill)}" style="border:0;background:transparent;color:inherit;cursor:pointer;padding:0 0 0 2px;line-height:1">${JP.icon('x', 11)}</button></span>`
          )
          .join('')
      : '<span class="chip chip--muted">No skills yet — add at least five.</span>';
    JP.$$('[data-remove-skill]', wrap).forEach((button) => {
      button.addEventListener('click', () => {
        skills = skills.filter((skill) => skill !== button.dataset.removeSkill);
        renderSkills();
        setDirty(true);
      });
    });
  }

  function addSkill(value) {
    const skill = String(value || '').trim().replace(/,$/, '');
    if (!skill) return;
    if (!skills.some((item) => item.toLowerCase() === skill.toLowerCase())) {
      skills.push(skill);
      renderSkills();
      setDirty(true);
    }
    const input = el('skill-input');
    if (input) input.value = '';
  }

  // --- repeaters ----------------------------------------------------------
  function experienceTemplate(item = {}) {
    return `<div class="repeater-item" data-exp>
      <div class="repeater-item__head">
        <span class="repeater-item__index">Role</span>
        <button class="repeater-remove" type="button" data-remove aria-label="Remove role">${JP.icon('trash', 15)}</button>
      </div>
      <div class="form-grid">
        <div class="field"><label>Title</label><input class="input" data-field="title" type="text" value="${JP.escapeAttr(item.title || '')}" placeholder="AI Engineer"></div>
        <div class="field"><label>Company</label><input class="input" data-field="company" type="text" value="${JP.escapeAttr(item.company || '')}" placeholder="Nexus Labs"></div>
      </div>
      <div class="form-grid form-grid--3">
        <div class="field"><label>Start</label><input class="input" data-field="start" type="text" value="${JP.escapeAttr(item.start || '')}" placeholder="2024"></div>
        <div class="field"><label>End</label><input class="input" data-field="end" type="text" value="${JP.escapeAttr(item.end || '')}" placeholder="present"></div>
        <div class="field"><label>Location</label><input class="input" data-field="location" type="text" value="${JP.escapeAttr(item.location || '')}" placeholder="Remote"></div>
      </div>
      <div class="field">
        <label>Bullets</label>
        <textarea class="textarea bullets-input" data-field="bullets" placeholder="One bullet per line. Start with an action and, only if true, a measurable result.">${JP.escapeHtml((item.bullets || []).join('\n'))}</textarea>
        <span class="field-hint">These exact lines are the evidence pool. Do not add claims you cannot defend.</span>
      </div>
      <div class="field">
        <label>Skills used</label>
        <input class="input" data-field="skills" type="text" value="${JP.escapeAttr((item.skills || []).join(', '))}" placeholder="Python, FastAPI, PostgreSQL">
      </div>
    </div>`;
  }

  function educationTemplate(item = {}) {
    return `<div class="repeater-item" data-edu>
      <div class="repeater-item__head">
        <span class="repeater-item__index">Education</span>
        <button class="repeater-remove" type="button" data-remove aria-label="Remove education">${JP.icon('trash', 15)}</button>
      </div>
      <div class="form-grid">
        <div class="field"><label>Degree</label><input class="input" data-field="degree" type="text" value="${JP.escapeAttr(item.degree || '')}" placeholder="BSc Computer Science"></div>
        <div class="field"><label>Institution</label><input class="input" data-field="institution" type="text" value="${JP.escapeAttr(item.institution || '')}" placeholder="University"></div>
      </div>
      <div class="form-grid">
        <div class="field"><label>Year</label><input class="input" data-field="year" type="text" value="${JP.escapeAttr(item.year || '')}" placeholder="2023"></div>
        <div class="field"><label>Detail</label><input class="input" data-field="detail" type="text" value="${JP.escapeAttr(item.detail || '')}" placeholder="Thesis, honours, relevant coursework"></div>
      </div>
    </div>`;
  }

  function bindRepeater(container) {
    JP.$$('[data-remove]', container).forEach((button) => {
      button.addEventListener('click', () => {
        button.closest('.repeater-item').remove();
        relabel(container);
        setDirty(true);
      });
    });
    JP.$$('input, textarea', container).forEach((input) => {
      input.addEventListener('input', () => setDirty(true));
    });
  }

  function relabel(container) {
    JP.$$('.repeater-item', container).forEach((item, index) => {
      const label = item.querySelector('.repeater-item__index');
      if (label) {
        const base = label.textContent.replace(/^#?\d+\s*·?\s*/, '');
        label.textContent = `#${index + 1} · ${base || 'Role'}`;
      }
    });
  }

  function addExperience(item = {}) {
    const container = el('experience-list');
    if (!container) return;
    container.insertAdjacentHTML('beforeend', experienceTemplate(item));
    const node = container.lastElementChild;
    bindRepeater(node);
    relabel(container);
    if (!item.title) {
      setDirty(true);
      node.querySelector('[data-field="title"]')?.focus();
    }
  }

  function addEducation(item = {}) {
    const container = el('education-list');
    if (!container) return;
    container.insertAdjacentHTML('beforeend', educationTemplate(item));
    const node = container.lastElementChild;
    bindRepeater(node);
    relabel(container);
    if (!item.degree) {
      setDirty(true);
      node.querySelector('[data-field="degree"]')?.focus();
    }
  }

  // --- completeness -------------------------------------------------------
  function collect() {
    const value = (id) => (el(id)?.value || '').trim();
    const experiences = JP.$$('[data-exp]', el('experience-list')).map((node) => ({
      title: node.querySelector('[data-field="title"]').value.trim(),
      company: node.querySelector('[data-field="company"]').value.trim(),
      start: node.querySelector('[data-field="start"]').value.trim(),
      end: node.querySelector('[data-field="end"]').value.trim(),
      location: node.querySelector('[data-field="location"]').value.trim(),
      bullets: node
        .querySelector('[data-field="bullets"]')
        .value.split('\n')
        .map((line) => line.replace(/^[-•*]\s*/, '').trim())
        .filter(Boolean),
      skills: splitList(node.querySelector('[data-field="skills"]').value),
    }));
    const education = JP.$$('[data-edu]', el('education-list')).map((node) => ({
      degree: node.querySelector('[data-field="degree"]').value.trim(),
      institution: node.querySelector('[data-field="institution"]').value.trim(),
      year: node.querySelector('[data-field="year"]').value.trim(),
      detail: node.querySelector('[data-field="detail"]').value.trim(),
    }));
    return {
      full_name: value('full_name') || 'Your Name',
      headline: value('headline'),
      email: value('email'),
      phone: value('phone'),
      location: value('location'),
      summary: value('summary'),
      skills,
      experience: experiences.filter((item) => item.title || item.company || item.bullets.length),
      education: education.filter((item) => item.degree || item.institution),
      links: splitList(value('links')),
      target_roles: splitList(value('target_roles')),
      preferred_locations: splitList(value('preferred_locations')),
      remote_preference: el('remote_preference')?.value || 'any',
      salary_floor: value('salary_floor'),
      work_authorization: value('work_authorization'),
      languages: splitList(value('languages')),
    };
  }

  function completenessChecks(data) {
    return [
      ['Full name set', Boolean(data.full_name && data.full_name !== 'Your Name')],
      ['Headline written', Boolean(data.headline)],
      ['Email added', Boolean(data.email)],
      ['Location added', Boolean(data.location)],
      ['Summary written', Boolean(data.summary)],
      ['At least 5 skills', data.skills.length >= 5],
      ['Experience added', data.experience.length > 0],
      ['Evidence bullets present', data.experience.some((item) => item.bullets.length > 0)],
      ['Target roles defined', data.target_roles.length > 0],
      ['Education added', data.education.length > 0],
    ];
  }

  function renderCompleteness(data, score) {
    const checks = completenessChecks(data);
    const computed = Math.round((checks.filter(([, ok]) => ok).length / checks.length) * 100);
    const value = score === undefined ? computed : score;
    const ring = el('completeness-ring');
    if (ring) ring.innerHTML = JP.scoreRing(value, 62);
    const copy = el('completeness-copy');
    if (copy) {
      copy.textContent =
        value >= 90 ? 'Excellent. The agent has enough evidence to tailor safely.' : `${checks.filter(([, ok]) => !ok).length} item(s) still missing for safe tailoring.`;
    }
    const list = el('completeness-list');
    if (list) {
      list.innerHTML = checks
        .map(
          ([label, ok]) =>
            `<div class="completeness-item ${ok ? '' : 'is-missing'}">${JP.icon(ok ? 'check' : 'x', 12)}<span>${JP.escapeHtml(label)}</span></div>`
        )
        .join('');
    }
    const sidebarMeta = document.querySelector('.sidebar-user__meta small');
    if (sidebarMeta) sidebarMeta.textContent = `${value}% profile complete`;
  }

  function renderEvidence(list) {
    const wrap = el('evidence-skills');
    if (!wrap) return;
    if (!list || !list.length) {
      wrap.innerHTML = '<p class="tiny faint">No skills detected yet. Add skills or bullets and save.</p>';
      return;
    }
    wrap.innerHTML = list
      .slice(0, 10)
      .map(
        (item) => `<div class="pipeline-row" style="padding:6px 0">
          <div class="pipeline-row__top"><span>${JP.escapeHtml(item.skill)}</span><span>${item.evidence_count}</span></div>
          <div class="progress"><span style="width:${JP.clamp(item.evidence_count * 14, 8, 100)}%"></span></div>
        </div>`
      )
      .join('');
  }

  // --- fill ---------------------------------------------------------------
  function fill(profile) {
    const set = (id, value) => {
      const input = el(id);
      if (input) input.value = value || '';
    };
    set('full_name', profile.full_name === 'Your Name' ? '' : profile.full_name);
    set('headline', profile.headline);
    set('email', profile.email);
    set('phone', profile.phone);
    set('location', profile.location);
    set('summary', profile.summary);
    set('remote_preference', profile.remote_preference || 'any');
    set('target_roles', (profile.target_roles || []).join(', '));
    set('preferred_locations', (profile.preferred_locations || []).join(', '));
    set('salary_floor', profile.salary_floor);
    set('work_authorization', profile.work_authorization);
    set('languages', (profile.languages || []).join(', '));
    set('links', (profile.links || []).join(', '));

    skills = (profile.skills || []).slice();
    renderSkills();

    const expContainer = el('experience-list');
    const eduContainer = el('education-list');
    if (expContainer) {
      expContainer.innerHTML = '';
      (profile.experience || []).forEach((item) => addExperience(item));
      if (!(profile.experience || []).length) expContainer.innerHTML = '<p class="tiny faint">No roles yet. Add your most recent role first.</p>';
    }
    if (eduContainer) {
      eduContainer.innerHTML = '';
      (profile.education || []).forEach((item) => addEducation(item));
      if (!(profile.education || []).length) eduContainer.innerHTML = '<p class="tiny faint">No education added yet.</p>';
    }
    renderExtra(profile);
    setDirty(false);
  }

  function renderExtra(profile) {
    const projects = el('projects-list');
    if (projects) {
      const items = profile.projects || [];
      projects.innerHTML = items.length
        ? `<span class="filter-group__label">Projects</span>${items
            .map(
              (item) =>
                `<div class="pipeline-row" style="padding:6px 0"><div class="pipeline-row__top"><span>${JP.escapeHtml(item.name || '')}</span></div><span class="tiny faint">${JP.escapeHtml(item.description || '')}</span></div>`
            )
            .join('')}`
        : '<p class="tiny faint">No projects extracted yet.</p>';
    }
    const certifications = el('certifications-list');
    if (certifications) {
      const items = profile.certifications || [];
      certifications.innerHTML = items.length
        ? `<span class="filter-group__label">Certifications</span>${items
            .map(
              (item) =>
                `<div class="pipeline-row" style="padding:6px 0"><div class="pipeline-row__top"><span>${JP.escapeHtml(item.name || '')}</span><span>${JP.escapeHtml(item.year || '')}</span></div><span class="tiny faint">${JP.escapeHtml(item.issuer || '')}</span></div>`
            )
            .join('')}`
        : '<p class="tiny faint">No certifications extracted yet.</p>';
    }
    const achievements = el('achievements-list');
    if (achievements) {
      const items = profile.achievements || [];
      achievements.innerHTML = items.length
        ? `<span class="filter-group__label">Achievements</span><ul class="notes-list">${items
            .map((item) => `<li>${JP.escapeHtml(item)}</li>`)
            .join('')}</ul>`
        : '<p class="tiny faint">No achievements extracted yet.</p>';
    }
  }

  async function load() {
    loading = true;
    try {
      const data = await JP.api('/api/profile');
      fill(data.profile || {});
      renderCompleteness(data.profile || {}, data.completeness);
      renderEvidence(data.skills || []);
    } catch (error) {
      JP.toast(error.message, { tone: 'error' });
    } finally {
      loading = false;
    }
  }

  // --- save ---------------------------------------------------------------
  async function save(buttons) {
    if (loading) return;
    const payload = collect();
    if (!payload.full_name || payload.full_name === 'Your Name') {
      JP.toast('Add your full name before saving.', { tone: 'error' });
      el('full_name')?.focus();
      return;
    }
    const targets = buttons || [el('save-top'), el('save-bottom')].filter(Boolean);
    targets.forEach((button) => {
      button.disabled = true;
      button.classList.add('is-loading');
    });
    try {
      const data = await JP.api('/api/profile', { method: 'PUT', body: payload });
      setDirty(false);
      renderCompleteness(data.profile || payload, data.completeness);
      const fresh = await JP.api('/api/profile');
      renderEvidence(fresh.skills || []);
      JP.toast('Profile saved. Matching and tailoring will use these facts.', { title: 'Profile updated' });
    } catch (error) {
      JP.toast(error.message, { tone: 'error', title: 'Could not save profile' });
    } finally {
      targets.forEach((button) => {
        button.disabled = false;
        button.classList.remove('is-loading');
      });
    }
  }

  // --- CV import ----------------------------------------------------------
  async function parseCv() {
    const text = (el('cv-text')?.value || '').trim();
    if (text.length < 40) {
      JP.toast('Paste at least 40 characters of CV text.', { tone: 'error' });
      return;
    }
    const button = el('parse-cv');
    if (button) {
      button.disabled = true;
      button.classList.add('is-loading');
    }
    try {
      const data = await JP.api('/api/profile/parse-cv', { method: 'POST', body: { text } });
      fill(data.profile || {});
      renderCompleteness(data.profile || {}, data.completeness);
      const fresh = await JP.api('/api/profile');
      renderEvidence(fresh.skills || []);
      JP.toast(data.message || 'CV parsed. Review every field before saving.', { title: 'CV imported' });
    } catch (error) {
      JP.toast(error.message, { tone: 'error', title: 'Could not parse CV' });
    } finally {
      if (button) {
        button.disabled = false;
        button.classList.remove('is-loading');
      }
    }
  }

  // --- boot ---------------------------------------------------------------
  function boot() {
    el('skill-add')?.addEventListener('click', () => addSkill(el('skill-input').value));
    el('skill-input')?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ',') {
        event.preventDefault();
        addSkill(event.target.value);
      }
    });
    el('add-experience')?.addEventListener('click', () => addExperience({}));
    el('add-education')?.addEventListener('click', () => addEducation({}));
    el('save-top')?.addEventListener('click', () => save());
    el('save-bottom')?.addEventListener('click', () => save());
    el('discard-changes')?.addEventListener('click', () => load());
    el('parse-cv')?.addEventListener('click', parseCv);
    document.addEventListener('resume:applied', () => {
      setDirty(false);
      load();
    });

    form()?.addEventListener('input', (event) => {
      if (event.target.closest('[data-exp], [data-edu]')) return;
      setDirty(true);
    });
    form()?.addEventListener('submit', (event) => {
      event.preventDefault();
      save();
    });
    window.addEventListener('beforeunload', (event) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = '';
    });

    renderSkills();
    load();
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
