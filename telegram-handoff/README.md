# Public Telegram prompt handoff

This buildless static page is deployed separately from the owner-protected
dashboard, on the existing Sites hosting service. It has no database, credentials,
inference, tracking code, external runtime dependencies or outgoing fetch calls.

- URL: https://newswire-prompt-handoff.shadmanulhaque.chatgpt.site/
- Site project: `appgprj_6ad209ff42dc819194c957e713ae4166`
- Deployed Site source commit: `69a9030e6c3a5e428dfdd011452631df6fe49d98`
- Deployed Site version: 4
- Canonical Sites checkout: `newswire-telegram-handoff`, beside this application.

The mirrored `dist/` and tests here keep the hosted contract reviewable with the
sender. Keep these files byte-identical when updating the Site, and publish an
exact-source archive through the Sites source helper and normal save/deploy flow.
The existing dashboard's audience must remain unchanged.

`#v=1&action=copy&prompt=<URI-component>` carries the complete prompt.
The sender's `build_prompt` is the only news prompt builder. This page reads that
text, allows edits and copies the current textarea value. Historical `codex` and
`claude` action values remain accepted for copying, without app buttons or launch
code. The fragment is not part of the HTTP request. Never move prompt text into
a server request, analytics or storage.

Run `node --test telegram-handoff/tests/handoff.test.mjs` from the application
root. Tests cover exact Unicode/line-break preservation, long prompts, clipboard
failure, deferred success, inert page load, edited prompts, historical URLs and
the absence of native app actions. The page has one Copy full prompt button and
manual-copy guidance. It does not submit prompts or invoke AI.
