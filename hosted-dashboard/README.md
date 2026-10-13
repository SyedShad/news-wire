# Owner-only hosted dashboard

This project is the owner frontend for Open Source AI News Wire.
It provides the complete owner workspace while keeping collection, SQLite,
generation and every canonical mutation on the laptop.

The local loopback dashboard remains the emergency operator fallback. The
laptop accepts no inbound internet connection.

## Access model

The first production release is owner-only:

1. ChatGPT Sites custom access controls who can open the project.
2. The application owner password is a second data-access gate.
3. The password is a generated 256-bit value. Its per-verifier salted
   PBKDF2-HMAC-SHA256 digest uses workerd's enforced maximum of 100,000
   iterations; the outer Sites gate, login throttling, and lockout provide
   independent online-attack controls.
4. Failed logins are throttled and locked out; sessions expire after eight
   hours; credential-version rotation invalidates existing sessions; logout
   removes the server-side token hash.

Google sign-in is not shown and is not part of the launch path. The retained
OAuth implementation is for a later, separately tested Workspace viewer release.
Every dashboard page, API, server-rendered payload, and export requires the
`master` role. Owner mutations additionally require a same-origin request and a
current bridge heartbeat.

## Data architecture

Bridge protocol v2.3.0 (schema v2) polls outbound every ten seconds. HMAC-SHA256 signs the
protocol version, timestamp, random nonce, method, path, and request-body
digest. Sites rejects stale timestamps, replayed nonces, oversized payloads,
invalid schemas, unsupported bridge versions, and operations outside the fixed
allowlist.

D1 stores only the minimum hosted service state:

- hashed authentication sessions and lockout/audit state;
- normalized projections for fetched articles, historical drafts, sources,
  operational notices, generation catalog/jobs, schedule, settings and diagnostics;
- authenticated generation artifact metadata and bounded base64 chunks; the
  runtime keeps canonical files and never exposes local paths;
- bridge heartbeat and protocol/runtime versions;
- five-minute idempotent owner commands with claimed/completed/failed results;
- one-minute on-demand historical read requests and bounded short-lived detail
  cache entries.
- encrypted owner-device Web Push subscriptions, immutable relevance events,
  per-device FIFO deliveries, and expiring action capabilities. Subscription
  endpoints and keys are never projected, logged, or exported.

The News inbox contains separate relevant source articles, ordered by first fetch
time across all history. There is no score, trust qualification, automatic
research or editorial approval gate. Each article opens its original source and
an independent **Generate content / Generate graphics** workspace. Each task
selects runtime models, an ordered list of installed skills, and customizations.
The last model/skill selections are remembered independently for each task.

`GENERATION_ENABLED` defaults to `false`. The layout and retained news history
can deploy before inference is ready: cached bridge 2.2 stories open their source
history, and their generation sections remain disabled. Missing fetch timestamps
fall back to the cached detection time. No machine review window hides these rows.
Bridge 2.3 catalog/article reads are queued only after that version is connected.
Set this flag to `true` on staging after local authentication and real text/graphics
acceptance, then on production after hosted acceptance. The server enforces it
for create, retry and input commands. Reads, cancellation and manual text saves
remain available without inference on the compatible bridge.

Jobs retain their input, progress, interactive questions, errors, outputs and
version history. Text can be copied or edited into a new version; graphics can be
previewed and downloaded. Hosted artifact transfer is limited to 25 MiB per
artifact with 500 KiB decoded chunks; downloads verify size and SHA256. HTML and
SVG are downloaded as attachments rather than rendered inline.

Operational Review is its own tab with unread badges and read controls for health,
correction, catch-up, recovery and generation notices. Notice actions do not affect articles.
Existing fetched-article external notification eligibility and delivery ledgers
are retained. The notification **Open news inbox** action opens the human
workspace; older automatic-research capabilities return 410 and queue no work.
Dismiss remains durable and replay-safe.

Cached information and outputs remain readable when the laptop sleeps. Generation
and other operational controls require a current heartbeat. Models and skills
come from the local Codex runtime, including skills available through that runtime.
ChatGPT website-only models or GPTs are not a separately accessible catalog.

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
| `GENERATION_ENABLED` | No | Inference activation switch; defaults false |
| `PUSH_VAPID_PUBLIC_KEY` | No | Browser Web Push application-server key |
| `PUSH_VAPID_PRIVATE_KEY` | Yes | Web Push signing key |
| `PUSH_VAPID_SUBJECT` | No | Owner contact URI for Web Push providers |
| `PUSH_SUBSCRIPTION_ENCRYPTION_KEY` | Yes | Encrypts subscription material at rest |
| `PUSH_ACTION_SECRET` | Yes | HMAC key for one-use notification actions |
| `PUSH_DELIVERY_ENABLED` | No | Independent hosted delivery kill switch; defaults false |

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
npm run auth:refresh-verifier
npm run auth:provision-bridge -- localhost:3000
npm run dev
```

The provisioning helpers store the owner password and bridge secret in macOS
Keychain and create an ignored, owner-readable `.env.local`. The owner password
itself is never written to the environment file.
`auth:refresh-verifier` keeps the existing Keychain password and safely
regenerates only its ignored local verifier when the hosting runtime changes.

## Laptop bridge operation

Run these from a verified immutable install that supports bridge 2.3.0, never from a Git worktree:

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

Before deploying this version, apply `drizzle/0007_article_generation.sql`. It
adds fetch-time indexing and expands historical read requests without deleting
existing pending reads. Preserve the earlier Sites version for rollback.

Generation uses the laptop's signed, ChatGPT-authenticated Codex app-server.
On the Mac, run `open-source-ai-news-wire generation login` and complete local
ChatGPT authentication. The hosted dashboard never receives ChatGPT credentials.
Use **Refresh models and skills** after login or skill changes. The dashboard
shows login, usage and generation failures without hiding fetched articles.
Publishing remains manual.
