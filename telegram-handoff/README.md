# Public Telegram prompt handoff

This buildless static page is deployed separately from the owner-protected
dashboard, on the existing Sites hosting service. It has no database, credentials,
inference, tracking code, external runtime dependencies or outgoing fetch calls.

- URL: https://newswire-prompt-handoff.shadmanulhaque.chatgpt.site/
- Site project: `appgprj_6ad209ff42dc819194c957e713ae4166`
- Deployed Site source commit: `12ee3819a1a9fd39e2cee39ef7952cae09752294`
- Deployed Site version: 5
- Canonical Sites checkout: `newswire-telegram-handoff`, beside this application.

The mirrored `dist/` and tests here keep the hosted contract reviewable with the
sender. Keep these files byte-identical when updating the Site, and publish an
exact-source archive through the Sites source helper and normal save/deploy flow.
The existing dashboard's audience must remain unchanged.

`#v=1&action=copy&prompt=<URI-component>` carries the complete prompt.
The sender's `build_prompt` remains the only news prompt builder. This page reads
that text and allows editing, copying, or explicitly opening a new native desktop
chat. Copy full prompt, Open with Codex and Open with Claude all read the current
textarea value. App links encode complete text and never submit it. Historical
`codex` and `claude` action values remain readable and inert on page load.

App handlers use the documented `codex://new?prompt=...` and
`claude://claude.ai/new?q=...` URLs. Empty/whitespace-only prompts disable app
opening. Claude is disabled above 14,000 UTF-16 code units, conservatively
respecting its approximately 14,000-character limit without truncation; complete
copying remains available. Missing app handlers never cause a claimed launch
success. Users may need browser/OS confirmation or an external browser.

The fragment is not part of the HTTP request. Never move prompt text into server
requests, analytics or storage. The page has no automatic copy, navigation,
submission or inference, and requires no dashboard access.

Run `node --test telegram-handoff/tests/handoff.test.mjs` from the application
root. Tests cover Unicode/line breaks, edited text, long prompts, clipboard
failure/deferred success, historical links, empty fields, native URL encoding
and unavailable-handler fallbacks. Native app launches remain a manual check;
browser automation previously blocked custom-scheme navigation. Do not bypass
that restriction or claim end-to-end native verification.
