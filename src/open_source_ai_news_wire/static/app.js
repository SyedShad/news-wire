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
})();
