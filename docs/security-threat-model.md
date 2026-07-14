# Security threat model

## Protected assets

- The local runtime corpus, editorial decisions, drafts, evidence passages, and usage ledger.
- The user's Codex authentication and other home-directory credentials.
- The Mac's private network, local services, filesystem, and notification surface.
- Editorial integrity: no unsupported claim, false verification, stale approval, or automatic publication.

## Trust boundaries

Public sources are untrusted input. Adapters normalize bounded responses and never execute source content. Requests are limited to registered HTTPS hosts, revalidate DNS and every redirect, reject private or special addresses, cap redirects, bytes, and time, and do not inherit proxy credentials or persist cookies.

The dashboard is loopback-only, random-port, and one-time-link protected. It shares the local database with the independent worker; keeping a browser open is not required.

Codex assistance is optional and receives only a minimal packet of public URLs, short passages, claims, evidence IDs, and task metadata. The invocation uses ignored user configuration, read-only tool policy, strict JSON output, and an outer macOS sandbox. Output is rejected for unknown evidence references, unsupported facts or URLs, unsafe markup, or tool instructions.

## Fail-closed controls

- A primary source or two genuinely independent reporting sources are required before qualification.
- Discovery traces may create bounded watches but cannot create drafts.
- Drafting requires explicit human approval and volatile-evidence revalidation.
- Material updates flag corrections and invalidate current drafts or approvals.
- Critical disk pressure blocks collection before partial writes.
- Failed isolation keeps assistance disabled while deterministic monitoring continues.
- Runtime data cannot be placed inside a Git worktree; repository scans exclude and detect runtime artifacts and secrets.
- Scheduler and application uninstall preserve data unless a separate purge plan is executed.

## Residual risks

RSS publishers can provide inaccurate timestamps or silently rewrite entries; source transactions, hashes, provenance, and correction alerts make this visible but cannot prevent it. Title-token clustering can miss paraphrases or occasionally join similar developments; human review remains the activation gate. Public-source terms and access can change after the registry's checked date; source failures degrade independently and require periodic operator review.

There is intentionally no product backup. Device loss, disk failure, or explicit purge can permanently remove the local corpus.
