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

export function mount(document, browser) {
  const field = document.getElementById('prompt');
  const copy = document.getElementById('copy');
  const status = document.getElementById('status');
  const say = message => { status.textContent = message; };
  function sync() {
    copy.disabled = !field.value;
  }
  try {
    const data = decodeHandoff(browser.location.hash);
    field.value = data.prompt;
    field.disabled = false;
    document.getElementById('heading').textContent = 'Copy your complete prompt.';
    say('Review or edit the prompt, then select Copy full prompt.');
    sync();
  } catch (error) {
    say(error.message);
  }
  field.addEventListener('input', () => { sync(); say('Prompt updated. Ready to copy.'); });
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
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') mount(document, window);
