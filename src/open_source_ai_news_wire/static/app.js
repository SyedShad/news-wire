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

  document.querySelectorAll('.review-form').forEach((form) => {
    form.addEventListener('submit', (event) => {
      const action = event.submitter?.value || '';
      if (!action.startsWith('manual_approve_')) return;
      const version = form.querySelector('[data-manual-confirmation-version]');
      if (version) version.value = '';
      const confirmed = window.confirm(
        'Qualification requirements have not passed. Manual approval will create a draft from the currently available sources. Continue?'
      );
      if (!confirmed) {
        event.preventDefault();
        return;
      }
      if (version) version.value = 'manual_override_v1';
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
          ? `Review inbox, ${status.unread_alerts} unread`
          : 'Review inbox');
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
