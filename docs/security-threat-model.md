# Security threat model

## Protected assets

- The local runtime corpus, editorial decisions, drafts, evidence passages, and usage ledger.
- The user's Codex authentication and other home-directory credentials.
- The Mac's private network, local services, filesystem, and notification surface.
- Editorial integrity: no unsupported claim, false verification, stale approval, or automatic publication.

## Trust boundaries

Public sources are untrusted input. Adapters normalize bounded responses and never execute source content. Requests are limited to registered HTTPS hosts, revalidate DNS and every redirect, reject private or special addresses, cap redirects, bytes, and time, and do not inherit proxy credentials or persist cookies.

The dashboard is loopback-only, random-port, and one-time-link protected. It shares the local database with the independent worker; keeping a browser open is not required.

Codex assistance is optional and receives only a minimal packet of public URLs, short passages, claims, evidence IDs, and task metadata. Release 0.3.7 verifies the ChatGPT-bundled Codex binary's Apple signature, OpenAI team ID, path, version, SHA-256, and CDHash. The invocation uses ignored user configuration, no-tools policy, strict JSON output, and an outer macOS sandbox that denies child processes and all network endpoints except one ephemeral loopback broker port.

The broker accepts strict CONNECT requests only for reviewed ChatGPT/OpenAI hostnames on port 443. It rejects credentials, IP literals, unsafe or special DNS answers, peer mismatches, malformed requests, and unreviewed hosts. It connects by validated numeric address while tunnelling TLS unchanged, so Codex still verifies the destination hostname and certificate. The broker and copied ChatGPT login are destroyed after each invocation.

## Fail-closed controls

- A primary source or two genuinely independent reporting sources are required before qualification.
- Discovery traces may create bounded watches but cannot create drafts.
- Drafting requires explicit human approval and volatile-evidence revalidation.
- Material updates flag corrections and invalidate current drafts or approvals.
- Critical disk pressure blocks collection before partial writes.
- Failed isolation keeps assistance disabled while deterministic monitoring continues.
- A 24-hour release-, schema-, OS-, binary-, and policy-bound attestation is required by the worker, CLI, dashboard, pilot readiness, and notification activation.
- A failed child-credential, Unix-socket, private-network, broker, or tool-event canary invalidates assistance and cannot be bypassed by manual enablement.
- Runtime data cannot be placed inside a Git worktree; repository scans exclude and detect runtime artifacts and secrets.
- Scheduler and application uninstall preserve data unless a separate purge plan is executed.

## Residual risks

RSS publishers can provide inaccurate timestamps or silently rewrite entries; source transactions, hashes, provenance, and correction alerts make this visible but cannot prevent it. Title-token clustering can miss paraphrases or occasionally join similar developments; human review remains the activation gate. Public-source terms and access can change after the registry's checked date; source failures degrade independently and require periodic operator review.

There is intentionally no product backup. Device loss, disk failure, or explicit purge can permanently remove the local corpus.
