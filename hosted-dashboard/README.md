# Owner-only hosted dashboard

This project is the ChatGPT Sites frontend for Open Source AI News Wire 0.4.1.
It provides the complete owner workspace while keeping collection, SQLite,
research, drafting, and every canonical mutation on the laptop.

The local loopback dashboard remains the emergency operator fallback. The
laptop accepts no inbound internet connection.

## Access model

The first production release is owner-only:

1. ChatGPT Sites custom access controls who can open the project.
2. The application owner password is a second data-access gate.
3. The generated password uses PBKDF2-HMAC-SHA256 with a per-verifier salt and
   at least 600,000 iterations.
4. Failed logins are throttled and locked out; sessions expire after eight
   hours; credential-version rotation invalidates existing sessions; logout
   removes the server-side token hash.

Google sign-in is not shown and is not part of the launch path. The retained
OAuth implementation is for a later, separately tested Workspace viewer release.
Every dashboard page, API, server-rendered payload, and export requires the
`master` role. Owner mutations additionally require a same-origin request and a
current bridge heartbeat.

## Data architecture

Bridge protocol v2 polls outbound every ten seconds. HMAC-SHA256 signs the
protocol version, timestamp, random nonce, method, path, and request-body
digest. Sites rejects stale timestamps, replayed nonces, oversized payloads,
invalid schemas, unsupported bridge versions, and operations outside the fixed
allowlist.

D1 stores only the minimum hosted service state:

- hashed authentication sessions and lockout/audit state;
- normalized, redacted projections for the story index, current story details,
  drafts and version history, sources, notices, schedule and usage, settings,
  and diagnostics;
- bridge heartbeat and protocol/runtime versions;
- five-minute idempotent owner commands with claimed/completed/failed results;
- one-minute on-demand historical read requests and bounded short-lived detail
  cache entries.

The story index supports Review Now, Older Context, All History, status/lane/kind
filters, priority/newest sorting, and cursor pagination. Historical details not
already projected are requested through the outbound queue. Cached information
shows its synchronization time when the laptop is asleep; operational controls
are disabled until the heartbeat is current.

The hosted interface intentionally has no **Stop bridge** action. Starting,
stopping, reinstalling, and recovering the only connection remain local-only.

## Production configuration

The D1 binding is `DB`. R2 is not required. Configure these Sites runtime
values, marking every credential as secret:

| Variable | Secret | Purpose |
|---|---:|---|
| `AUTH_BASE_URL` | No | Exact production HTTPS origin |
| `AUTH_SECRET` | Yes | Session, fingerprint, and audit HMAC key |
| `MASTER_PASSWORD_VERIFIER` | Yes | Slow salted owner-password verifier |
| `MASTER_PASSWORD_VERSION` | No | Credential-rotation version |
| `BRIDGE_SECRET` | Yes | Laptop-to-Sites HMAC key |

Do not configure an owner password in plaintext. Do not place credentials in
source control, deployment descriptions, command output, or chat.

Because custom Sites access also guards machine endpoints, provision the
project's Sites machine-access token into the laptop's macOS Keychain through
the local bridge's standard-input setup path. The token is separate from
`BRIDGE_SECRET`, is never a Sites runtime variable, and is sent only in the
`OAI-Sites-Authorization` request header.

## Local validation

Requirements: Node.js 22.13+, Python 3.12+, the News Wire runtime database, and
macOS Keychain.

```bash
npm ci
npm run lint
npm test
```

For local authentication and bridge development:

```bash
npm run auth:provision-local
npm run auth:provision-bridge -- localhost:3000
npm run dev
```

The provisioning helpers store the owner password and bridge secret in macOS
Keychain and create an ignored, owner-readable `.env.local`. The owner password
itself is never written to the environment file.

## Laptop bridge operation

Run these from a verified immutable 0.4.1 install, never from a Git worktree:

```bash
open-source-ai-news-wire hosted-bridge configure \
  --url https://sentient-ai-news-wire.shadmanulhaque.chatgpt.site
open-source-ai-news-wire hosted-bridge once
open-source-ai-news-wire hosted-bridge install
open-source-ai-news-wire hosted-bridge status
```

The LaunchAgent has restart-on-failure behavior and redacted stdout/stderr logs
under the runtime operations directory. `status` distinguishes installed,
loaded, running, stopped, and failed states. `start` kick-starts a loaded but
stopped service.

Recovery order:

1. Wake the laptop and confirm it has internet access.
2. Run `hosted-bridge once` and verify one signed synchronization.
3. Run `hosted-bridge start`; reinstall only if the plist is missing or invalid.
4. Use the local loopback dashboard if Sites is unavailable.
5. If a hosted release fails verification, stop the bridge locally and redeploy
   the previous Sites version. Additive projection tables may remain.

## Exact-source deployment

Production must be saved and deployed only from the exact tested source commit:

1. run hosted lint/build/tests and the complete local News Wire suite;
2. commit the reviewed worktree and push the owner repository;
3. push the `hosted-dashboard` subtree to the Sites-managed source repository;
4. configure runtime variables and custom owner-only access;
5. save one Sites version bound to that commit and deploy it privately;
6. verify migrations, unauthenticated denial, owner login, current heartbeat,
   cross-device rendering, exports, and a harmless `bridge.healthcheck` command;
7. retain the previous Sites version for rollback.

Assisted drafting is a separate release-bound gate. Run a fresh isolation and
search-canary check after installing 0.4.1, and enable assistance only if
identity, isolation, and canary checks all pass. Dashboard access remains useful
when assistance is disabled.
