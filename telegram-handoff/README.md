# Public Telegram prompt handoff

This buildless static page is deployed separately from the owner-protected
dashboard, on the existing Sites hosting service. It has no database, credentials,
inference, tracking code, external runtime dependencies or outgoing fetch calls.

- URL: https://newswire-prompt-handoff.shadmanulhaque.chatgpt.site/
- Site project: `appgprj_6ad209ff42dc819194c957e713ae4166`
- Deployed Site source commit: `494b380a887a50374bf8ed94324bc57755f5bbe9`
- Deployed Site version: 3
- Canonical Sites checkout: `newswire-telegram-handoff`, beside this application.

The mirrored `dist/` and tests here keep the hosted contract reviewable with the
sender. Keep these files byte-identical when updating the Site, and publish an
exact-source archive through the Sites source helper and normal save/deploy flow.
The existing dashboard's audience must remain unchanged.

`#v=1&action=copy|codex|claude&prompt=<URI-component>` carries the complete prompt.
The sender's `build_prompt` is the only news prompt builder. This page only reads
that text, allows the viewer to edit it, and uses the same current textarea value
for copying and both documented app links. The fragment is not part of the HTTP
request. Never move prompt text into a server request, analytics or storage.

Run `node --test telegram-handoff/tests/handoff.test.mjs` from the application
root. Browser testing confirmed public loading and the clipboard-success UI.
Automated tests cover exact Unicode/line-break preservation, clipboard failure,
deferred success, inert page load, edited prompts and missing app-handler errors.
Native app launch remains a manual check: browser automation blocked custom
scheme navigation. Do not work around that restriction or claim a verified launch.
