(() => {
  const dismiss = (button) => button.closest('.flash')?.remove();
  document.querySelectorAll('[data-dismiss]').forEach((button) => {
    button.addEventListener('click', () => dismiss(button));
  });

  document.querySelectorAll('[data-copy-text]').forEach((button) => {
    button.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copyText || '');
        const original = button.textContent;
        button.textContent = 'Copied';
        window.setTimeout(() => { button.textContent = original; }, 1400);
      } catch (_error) {
        button.textContent = 'Copy failed';
      }
    });
  });

  const refreshStatus = async () => {
    if (document.hidden) return;
    try {
      const response = await fetch('/status.json', {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) return;
      const status = await response.json();
      document.querySelectorAll('[data-live-unread]').forEach((node) => {
        node.textContent = String(status.unread_alerts);
        node.hidden = status.unread_alerts === 0;
      });
      document.querySelectorAll('[data-live-inbox-link]').forEach((node) => {
        node.setAttribute('aria-label', status.unread_alerts
          ? `Operational Review, ${status.unread_alerts} unread`
          : 'Operational Review');
      });
      document.querySelectorAll('[data-live-queue]').forEach((node) => {
        node.textContent = String(status.queued_work);
      });
      document.querySelectorAll('[data-live-schedule]').forEach((node) => {
        node.textContent = String(status.schedule_status).replaceAll('_', ' ');
      });
    } catch (_error) {
      // A stopped or sleeping local server is an expected lifecycle state.
    }
  };
  window.setInterval(refreshStatus, 30_000);

  const draftPanel = document.querySelector('[data-draft-status-url]');
  const draftAutoOpen = draftPanel?.dataset.draftAutoOpen === 'true';
  if (draftPanel && (draftPanel.dataset.draftActive === 'true' || draftAutoOpen)) {
    let draftTabWasHidden = document.hidden;
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) draftTabWasHidden = true;
    });
    const refreshDraft = async () => {
      if (document.hidden) {
        draftTabWasHidden = true;
        window.setTimeout(refreshDraft, 2_000);
        return;
      }
      try {
        const response = await fetch(draftPanel.dataset.draftStatusUrl, {
          credentials: 'same-origin',
          headers: { Accept: 'application/json' },
        });
        if (!response.ok) {
          window.setTimeout(refreshDraft, 4_000);
          return;
        }
        const status = await response.json();
        draftPanel.querySelector('[data-draft-status-label]')?.replaceChildren(status.label || 'Draft update');
        draftPanel.querySelector('[data-draft-status-message]')?.replaceChildren(status.message || '');

        const retry = draftPanel.querySelector('[data-draft-retry]');
        if (retry) retry.hidden = !status.retryable;
        const review = draftPanel.querySelector('[data-draft-review]');
        if (review && status.draft_url) {
          review.href = status.draft_url;
          review.hidden = false;
        }
        if (draftAutoOpen && status.status === 'draft_ready' && status.draft_url && !draftTabWasHidden && !document.hidden) {
          window.location.assign(status.draft_url);
          return;
        }
        if (status.active) window.setTimeout(refreshDraft, 2_000);
      } catch (_error) {
        // A sleeping Mac or stopped local dashboard can interrupt polling.
        window.setTimeout(refreshDraft, 4_000);
      }
    };
    window.setTimeout(refreshDraft, 750);
  }
})();

(() => {
  const workspace = document.querySelector('[data-generation-workspace]');
  if (!workspace) return;
  let dirty = false;
  workspace.addEventListener('input', () => { dirty = true; });
  const forms = [...workspace.querySelectorAll('[data-generation-form]')];
  const selected = new Map(forms.map(form => [form, []]));
  let catalog = null;
  const renderSkills = (form) => {
    const ids = selected.get(form);
    form.querySelector('[data-skill-ids]').value = JSON.stringify(ids);
    const list = form.querySelector('[data-selected-skills]');
    list.replaceChildren();
    ids.forEach((id, index) => {
      const item = document.createElement('li');
      const name = document.createElement('span');
      name.textContent = catalog.skills.find(skill => skill.id === id)?.name || id;
      item.append(name);
      [['↑', -1], ['↓', 1], ['Remove', 0]].forEach(([label, delta]) => {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'button button-small'; button.textContent = label;
        button.disabled = delta && (index + delta < 0 || index + delta >= ids.length);
        button.setAttribute('aria-label', `${label} ${name.textContent}`);
        button.addEventListener('click', () => {
          dirty = true;
          if (!delta) ids.splice(index, 1);
          else [ids[index], ids[index + delta]] = [ids[index + delta], ids[index]];
          renderSkills(form);
        });
        item.append(button);
      });
      list.append(item);
    });
  };
  const loadCatalog = async (force = false) => {
    try {
      const response = await fetch(workspace.dataset.catalogUrl + (force ? '?refresh=1' : ''), {credentials: 'same-origin'});
      if (!response.ok) throw new Error('Could not load skills and models.');
      const hadCatalog = Boolean(catalog?.models?.length);
      catalog = await response.json();
      workspace.querySelector('[data-catalog-error]').textContent = catalog.error || '';
      forms.forEach(form => {
        const preference = catalog.preferences?.[form.dataset.task] || {};
        const models = form.querySelector('[data-model-select]');
        const chosenModel = hadCatalog ? models.value : (preference.model || catalog.default_model);
        models.replaceChildren();
        (catalog.models || []).forEach(model => {
          const option = new Option(model.display_name || model.id, model.id);
          option.selected = model.id === chosenModel;
          models.add(option);
        });
        if (!models.options.length) models.add(new Option('Models unavailable', ''));
        const picker = form.querySelector('[data-skill-select]');
        picker.replaceChildren(new Option('Choose a skill', ''));
        (catalog.skills || []).forEach(skill => {
          const option = new Option(skill.name, skill.id); option.title = skill.description || '';
          picker.add(option);
        });
        selected.set(form, (hadCatalog ? selected.get(form) : (preference.skill_ids || [])).filter(id => catalog.skills.some(skill => skill.id === id)));
        const generateButton = form.querySelector('[data-generate-button]') || form.querySelector('button[type="submit"]');
        if (generateButton) generateButton.disabled = !catalog.ready || !catalog.models?.length || !catalog.skills?.length;
        renderSkills(form);
      });
      if (catalog.refreshing || !catalog.models?.length || !catalog.skills?.length) window.setTimeout(() => loadCatalog(), 5000);
    } catch (error) {
      workspace.querySelector('[data-catalog-error]').textContent = error.message;
      window.setTimeout(() => loadCatalog(), 8000);
    }
  };
  workspace.querySelector('[data-refresh-catalog]')?.addEventListener('click', () => loadCatalog(true));
  forms.forEach(form => {
    form.querySelector('[data-request-id]').value = crypto.randomUUID();
    form.querySelector('[data-add-skill]').addEventListener('click', () => {
      const value = form.querySelector('[data-skill-select]').value;
      if (value && !selected.get(form).includes(value)) { selected.get(form).push(value); dirty = true; renderSkills(form); }
    });
    form.addEventListener('submit', event => {
      if (!selected.get(form).length) {
        event.preventDefault();
        workspace.querySelector('[data-catalog-error]').textContent = 'Choose at least one skill for this task.';
      }
    });
  });
  workspace.querySelectorAll('[data-copy-output]').forEach(button => button.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(button.closest('form').querySelector('[data-output-text]').value); button.textContent = 'Copied'; }
    catch (_) { button.textContent = 'Copy failed'; }
  }));
  const active = [...workspace.querySelectorAll('[data-job-status]')].filter(node => ['queued', 'running', 'needs_input'].includes(node.dataset.jobStatus));
  const refreshJobs = async () => {
    if (document.hidden) { window.setTimeout(refreshJobs, 3000); return; }
    try {
      const response = await fetch(workspace.dataset.jobsUrl, {credentials: 'same-origin'});
      if (!response.ok) throw new Error('Job status unavailable');
      const data = await response.json();
      const changed = active.some(node => data.jobs.some(job => job.id === node.dataset.jobId && job.status !== node.dataset.jobStatus));
      if (changed) {
        if (!dirty) { location.reload(); return; }
        const message = workspace.querySelector('[data-catalog-error]');
        message.textContent = 'Generation updated. Your edits are preserved; refresh after saving to see the latest output.';
        return;
      }
    } catch (_) { /* Offline workers retain their durable job status. */ }
    window.setTimeout(refreshJobs, 3000);
  };
  loadCatalog();
  if (active.length) window.setTimeout(refreshJobs, 3000);
})();
