export function decodeHandoff(hash) {
  const params = new URLSearchParams(hash.replace(/^#/, ''));
  const prompt = params.get('prompt');
  const action = params.get('action');
  // Historical app-action links remain readable as complete-text copy pages.
  if (params.get('v') !== '1' || !prompt || !['copy','codex','claude'].includes(action)) {
    throw new Error('This link is missing its complete prompt. Open it again from the NewsWire alert.');
  }
  return { prompt, action };
}

export async function copyFullPrompt(prompt, clipboard) {
  if (!clipboard?.writeText) throw new Error('Clipboard access is unavailable. Select the complete prompt and press Copy, ⌘C or Ctrl+C.');
  await clipboard.writeText(prompt);
}

export function nativeLink(app, prompt) {
  if (!prompt.trim()) throw new Error('Enter a prompt first.');
  if (app === 'codex') return 'codex://new?prompt=' + encodeURIComponent(prompt);
  if (app === 'claude') {
    if (prompt.length > 14000) throw new Error('This prompt exceeds Claude’s link limit. Copy the full prompt and paste it into Claude instead.');
    return 'claude://claude.ai/new?q=' + encodeURIComponent(prompt);
  }
  throw new Error('Unsupported desktop app.');
}

export function mount(document, browser) {
  const field = document.getElementById('prompt');
  const copy = document.getElementById('copy');
  const status = document.getElementById('status');
  const limit = document.getElementById('app-limit');
  const apps = {codex:document.getElementById('codex'),claude:document.getElementById('claude')};
  const say = message => { status.textContent = message; };
  function sync() {
    copy.disabled = !field.value;
    apps.codex.disabled = !field.value.trim();
    apps.claude.disabled = !field.value.trim() || field.value.length > 14000;
    limit.hidden = field.value.length <= 14000;
    limit.textContent = limit.hidden ? '' : 'This prompt exceeds Claude’s link limit. Copy the full prompt and paste it into Claude instead; it will not be truncated.';
  }
  try {
    const data = decodeHandoff(browser.location.hash);
    field.value = data.prompt;
    field.disabled = false;
    document.getElementById('heading').textContent = 'Copy your complete prompt.';
    say('Review or edit the prompt, then copy it or choose an app. Nothing is submitted automatically.');
    sync();
  } catch (error) {
    say(error.message);
  }
  field.addEventListener('input', () => { sync(); say('Prompt updated. Ready to copy or open in an app.'); });
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
  for (const [app, button] of Object.entries(apps)) {
    button.addEventListener('click', () => {
      try {
        if (button.disabled) throw new Error('App action unavailable. Copy the full prompt instead.');
        browser.location.assign(nativeLink(app, field.value));
        say(`Opening requested. Confirm any browser or operating-system prompt. If ${app === 'codex' ? 'Codex' : 'Claude'} does not open, use Copy full prompt.`);
      } catch (_) {
        say('Could not request opening the app. Use Copy full prompt, then paste it into your app.');
      }
    });
  }
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') mount(document, window);
