export function decodeHandoff(hash) {
  const params = new URLSearchParams(hash.replace(/^#/, ''));
  const prompt = params.get('prompt');
  const action = params.get('action');
  if (params.get('v') !== '1' || !prompt || !['copy','codex','claude'].includes(action)) {
    throw new Error('This handoff link is missing its complete prompt. Open it again from the NewsWire alert.');
  }
  return { prompt, action };
}

export function nativeLink(app, prompt) {
  if (app === 'codex') return 'codex://new?prompt=' + encodeURIComponent(prompt);
  if (app === 'claude') {
    if (prompt.length > 14000) throw new Error('This prompt exceeds Claude’s documented link limit. Copy the full prompt and paste it into Claude instead.');
    return 'claude://claude.ai/new?q=' + encodeURIComponent(prompt);
  }
  throw new Error('Unsupported desktop app.');
}

export async function copyFullPrompt(prompt, clipboard) {
  if (!clipboard?.writeText) throw new Error('Clipboard access is unavailable. Select the complete prompt and press Copy, ⌘C or Ctrl+C.');
  await clipboard.writeText(prompt);
}

export function mount(document, browser) {
  const field = document.getElementById('prompt');
  const copy = document.getElementById('copy');
  const status = document.getElementById('status');
  const limit = document.getElementById('app-limit');
  const links = {codex:document.getElementById('codex'),claude:document.getElementById('claude')};
  const say = message => { status.textContent = message; };
  function sync() {
    copy.disabled = !field.value;
    limit.hidden = field.value.length <= 14000;
    limit.textContent = limit.hidden ? '' : 'This prompt exceeds Claude’s documented link limit. Copy the full prompt and paste it into Claude instead; it will not be truncated.';
    for (const [app, link] of Object.entries(links)) {
      try {
        const url = nativeLink(app, field.value);
        if (!field.value) throw new Error('Enter a prompt first.');
        link.href = url;
        link.removeAttribute('aria-disabled');
      } catch (_) {
        link.removeAttribute('href');
        link.setAttribute('aria-disabled','true');
      }
    }
  }
  try {
    const data = decodeHandoff(browser.location.hash);
    field.value = data.prompt;
    field.disabled = false;
    document.getElementById('heading').textContent = data.action === 'copy' ? 'Copy your complete prompt.' : `Continue in ${data.action === 'codex' ? 'Codex' : 'Claude'}.`;
    say('Review the prompt, choose your skill, then select an action.');
    sync();
  } catch (error) {
    say(error.message);
  }
  field.addEventListener('input', () => { sync(); say('Prompt updated. Nothing has been submitted.'); });
  copy.addEventListener('click', async () => {
    const value = field.value;
    copy.disabled = true;
    say('Copying…');
    try {
      await copyFullPrompt(value, browser.navigator.clipboard);
      say('Full prompt copied. Ready to paste.');
    } catch (_) {
      field.focus(); field.select();
      say('Copy failed. The complete prompt is selected: press Copy, ⌘C or Ctrl+C.');
    } finally {
      sync();
    }
  });
  for (const [app, link] of Object.entries(links)) {
    link.addEventListener('click', event => {
      event.preventDefault();
      try {
        if (link.getAttribute('aria-disabled') === 'true') throw new Error('App link unavailable. Copy the complete prompt instead.');
        browser.location.assign(nativeLink(app, field.value));
        say(`Opening requested. Confirm any browser or operating-system prompt. If ${app === 'codex' ? 'Codex' : 'Claude'} does not open, use Copy full prompt.`);
      } catch (error) {
        say(error.message || 'Could not open the app. Use Copy full prompt.');
      }
    });
  }
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') mount(document, window);
