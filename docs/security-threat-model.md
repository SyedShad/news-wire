# Security threat model

## Protected assets

- The local runtime corpus, editorial decisions, drafts, evidence passages, and usage ledger.
- The user's Codex authentication and other home-directory credentials.
- The Mac's private network, local services, filesystem, and notification surface.
- Web Push subscription endpoints and keys, action capabilities, and full preview text.
- Editorial integrity: no unsupported claim, leaked internal warning, stale content packet, or automatic publication.

## Trust boundaries

Public sources are untrusted input. Adapters normalize bounded responses and never execute source content. Requests are limited to registered HTTPS hosts, revalidate DNS and every redirect, reject private or special addresses, cap redirects, bytes, and time, and do not inherit proxy credentials or persist cookies.

The local fallback dashboard is loopback-only, random-port, and one-time-link
protected. It shares the local database with the independent worker; keeping a
browser open is not required.

The owner-only hosted dashboard is a separate trust boundary. ChatGPT Sites
stores hashed sessions, a redacted normalized read projection, bounded
short-lived detail caches, bridge health, one-time nonces, and expiring command
and read queues. The laptop initiates every HTTPS request and exposes no inbound
listener. HMAC signatures bind timestamp, nonce, method, path, and body digest;
the hosted service rejects replay, stale requests, oversized payloads,
unsupported schema/runtime versions, and commands outside the fixed allowlist.
Remote controls are disabled without a current heartbeat.
The application password is a generated 256-bit value. Its salted
PBKDF2-HMAC-SHA256 verifier uses the workerd runtime's enforced 100,000-iteration
maximum; private Sites access, throttling, lockout, credential-versioned
sessions, and logout are independent controls around that verifier.
Sites sandboxed owner forms may serialize their origin as `null`; those requests
are accepted only when their URL matches the configured production origin,
browser fetch metadata reports `same-origin`, and Sites supplies both its
authenticated user and internal dispatch headers. Cross-site forms remain
rejected before password verification or mutation processing.
The custom Sites perimeter independently requires a machine-access bearer token
for bridge requests. That token and the HMAC secret use separate macOS Keychain
services and never appear in source, local JSON configuration, LaunchAgent
metadata, projection payloads, or redacted logs.

Web Push subscriptions are bearer capabilities encrypted at rest with a
dedicated AES-GCM key. VAPID signing, subscription encryption, push actions,
bridge HMAC, application sessions, and Sites machine access use separate
secrets. The server stores only HMACs of random 72-hour action capabilities and
binds each one to a single delivery, device, event, story, and action. Provider
404/410 responses disable the affected subscription; transient failures retry
within the bounded recovery window. Registration and revocation require a
current master session and same-origin request. Notification actions remain
usable without the eight-hour browser session but accept only Start research or
Dismiss and are idempotent.

Codex assistance is optional and receives only bounded story and source packets. Release 0.4.1 verifies the ChatGPT-bundled Codex binary's Apple signature, OpenAI team ID, path, version, SHA-256, and CDHash. Fresh source discovery uses a separate invocation that enables only secured web search; final writing keeps the no-tools policy. Both use ignored user configuration, strict JSON output, and an outer macOS sandbox that denies child processes and all network endpoints except one ephemeral loopback broker port.

The broker accepts strict CONNECT requests only for an explicit, reviewed set of ChatGPT/OpenAI hostnames on port 443. The set contains no wildcard or parent-domain grants; the regional `oaiusercontent.com` service names required by the signed bundled client are listed individually. Analytics and feedback are disabled, and `ab.chatgpt.com` remains blocked. The broker rejects credentials, IP literals, unsafe or special DNS answers, peer mismatches, malformed requests, and every unreviewed host. It connects by validated numeric address while tunnelling TLS unchanged, so Codex still verifies the destination hostname and certificate. The broker and copied ChatGPT login are destroyed after each invocation.

## Fail-closed controls

- Every configured source is explicitly trusted or research-required. Research-required detections run bounded automatic research and retain a dashboard-only verification warning.
- Content creation is available for every active story and begins only after the user selects Create content.
- Each content request runs a distinct fresh source search through the public-HTTPS, DNS, redirect, byte, and deadline boundary; failure never removes the editable shell.
- Final Reddit publication is manual and no publishing credentials are stored.
- Material updates flag corrections; every fresh content request refreshes to the latest story revision before writing.
- Critical disk pressure blocks collection before partial writes.
- Failed isolation keeps assistance disabled while deterministic monitoring continues.
- Relevance notifications default to shadow mode with independent local and hosted kill switches and fresh activation watermarks.
- A 24-hour release-, schema-, OS-, binary-, and policy-bound attestation is required by the worker, CLI, dashboard, pilot readiness, and notification activation.
- A failed child-credential, Unix-socket, private-network, broker, or tool-event canary invalidates assistance and cannot be bypassed by manual enablement.
- Runtime data cannot be placed inside a Git worktree; repository scans exclude and detect runtime artifacts and secrets.
- Scheduler and application uninstall preserve data unless a separate purge plan is executed.

## Residual risks

RSS publishers can provide inaccurate timestamps or silently rewrite entries; source transactions, hashes, provenance, and correction alerts make this visible but cannot prevent it. Title-token clustering can miss paraphrases or occasionally join similar developments; human review before manual publication remains essential. Public-source terms and access can change after the registry's checked date; source failures degrade independently and require periodic operator review.

There is intentionally no product backup. Device loss, disk failure, or explicit purge can permanently remove the local corpus. The hosted projection is not a backup and can be stale while the laptop sleeps. Compromise of both Sites owner access and the application password could expose the redacted projection or enqueue allowed commands during a current heartbeat, so password rotation, lockout, private Sites access, short command expiry, audit results, and local-only bridge shutdown remain required controls.

Full notification headlines and context can be exposed by operating-system lock
screen previews. The owner must accept this device-level privacy tradeoff when
enabling notifications. ChatGPT Sites private-wrapper support for service
workers, background push, and sessionless same-origin actions remains a
production-canary requirement rather than an assumed platform guarantee.
