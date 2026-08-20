---
name: open-source-ai-news-wire
description: Operate and inspect the local Open Source AI News Wire for current open-ecosystem and AGI developments plus privacy, security, and regulation AI news. Use for status, scans, source trust and health, automatic research, the local dashboard, scheduling, catch-up, purge, and manual Reddit content preparation.
---

# Open Source AI News Wire

Operate the local, destination-neutral monitoring and content-preparation system. Treat its SQLite store and dashboard as the source of truth; do not substitute an unrelated web search for a configured scan.

## Start safely

1. Resolve the installed command with `command -v open-source-ai-news-wire`; in a source checkout use `.venv/bin/open-source-ai-news-wire`.
2. Run `open-source-ai-news-wire status` before changing state.
3. Read [references/operator-commands.md](references/operator-commands.md) for lifecycle operations.
4. Read [references/editorial-policy.md](references/editorial-policy.md) before reviewing or drafting.

## Choose the workflow

- For current coverage, source health, queue state, or usage, inspect `status`, `sources health`, or launch `dashboard`.
- Treat configured first-party, government, original-research, and vetted publications as trusted. Their detections become Ready and receive the Urgent score floor immediately.
- Treat aggregators, social and discovery feeds, and unknown publishers as research-required. Background research runs automatically and receives the same floor at every terminal outcome. Do not ask for editorial approval.
- Use **Create content** when the user wants a Reddit draft. It always creates an editable shell first and records a distinct fresh-search attempt. Do not add qualification, approval, evidence, research, importance, lens, or manual-override steps.
- For fresh collection, use `schedule run-now` or `scan --trigger manual`. Overlapping scans coalesce.
- For missed time, use the default 72-hour recovery. Use explicit catch-up dates only when the user selects an older interval.
- For assistance, require a current v0.4.3 release-bound isolation/search attestation. A failed check leaves assistance disabled; deterministic monitoring and manual shell editing continue.
- For deletion, create a purge preview first and execute only the exact returned plan after explicit confirmation. Purge has no undo.

## Preserve boundaries

- Broader AI News must directly concern privacy, personal data, surveillance, data protection, cybersecurity, vulnerabilities, breaches, attacks, misuse, security incidents, regulation, legislation, regulators, courts, compliance, copyright, or policy enforcement.
- Leave Open Ecosystem News and AGI Development coverage unchanged.
- Exclude generic funding, acquisitions, chips, launches, research, and safety from Broader AI News unless the development directly intersects an allowed area.
- Keep unsafe URL rejection, duplicate active-work prevention, archived/withdrawn state, CSRF, loopback access, and manual publishing.
- Never add an OpenAI API key, paid fallback, publishing credential, browser cookie, proxy, hosted corpus, or destination integration.
- Treat `Verify this yourself` as dashboard-only. Never place it or other internal trust/research labels into copied or exported content.
- Preserve First-Public Time during catch-up and distinguish source revisions from material claim updates.

## Reddit content contract

Follow the repository's v0.4 Reddit mode: concise factual title, value-first casual body, Reddit Markdown, natural source links, a genuine open-ended engagement prompt, suggested flair when inferable, and the exact reminder `Verify rules before posting`. Do not assume a subreddit. The user reviews and publishes manually.

## Return useful results

Lead with last scan, next scan, queue, source failures, and Ready/Researching/Content-ready counts. For a story, distinguish trust classification, automatic research result, source provenance, supported facts, attributed claims, and remaining uncertainty. Link only stored safe sources.
