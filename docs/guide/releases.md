# Releases

Open Source AI News Wire uses semantic application versions and explicit database schema versions. A release can change the application without changing the database schema.

## Current release

| Item | Value |
|---|---|
| Application | 0.4.2 |
| Database schema | 9 |
| Release tag | Pending local validation and source release |
| Validated source commit | Bound by the local validation report at install time |
| Distribution | Owner source repository; no prebuilt binary attached |

Release 0.4.2 adds owner-only relevance notifications for every new relevant
source item, independent of quality and priority, while keeping detection on
the laptop and final publication manual.

Highlights:

- Trusted detections receive an immediate Urgent floor; research-required detections research automatically and receive the same floor at any terminal outcome.
- Every active story has one Create content action that creates a shell before its own fresh search.
- Broader AI News is restricted to privacy, security, and regulation.
- Reddit output follows one source-linked style and remains manually edited and published.
- The reviewed identity pin is `codex-cli 0.146.0-alpha.9.2`; source discovery permits only web search, and final writing permits no tools.
- The ChatGPT Sites frontend uses a redacted hosted read projection and expiring
  relay queues; SQLite and every canonical mutation remain on the laptop.
- Browser notifications show source-derived context. Start working runs only a
  fresh source search; it never creates a draft, and Dismiss changes only the
  notification.

See the complete [changelog](../../CHANGELOG.md) for the file-level release record.

## Check the installed version

The **Settings & data** page displays the application version and database information.

From a source checkout, you can also run:

```bash
uv run python -c \
  "from open_source_ai_news_wire import __version__; print(__version__)"
```

Application status and source health are available through:

```bash
uv run open-source-ai-news-wire status
uv run open-source-ai-news-wire sources health
```

## Update a source-checkout installation

Stop any open on-demand dashboard before updating. The scheduler can be paused if you want to prevent a scan during the update.

```bash
uv run open-source-ai-news-wire schedule pause
git switch main
git pull --ff-only origin main
uv sync --locked
uv run open-source-ai-news-wire migrate
uv run open-source-ai-news-wire schedule resume
```

Then verify:

```bash
uv run open-source-ai-news-wire status
uv run open-source-ai-news-wire sources health
uv run open-source-ai-news-wire dashboard
```

The migration operates on the configured local data root. The application does not create a backup before migration. Pause scheduling and snapshot the database before moving to schema 9, then run a fresh isolation/search canary before resuming assistance.

If the schedule was installed with the checkout's `.venv/bin/open-source-ai-news-wire` launcher, `uv sync --locked` updates that same environment and no schedule reinstall is normally required.

## Immutable maintainer installs

The application also supports immutable local releases with an atomic current-version switch. That path requires a clean commit reachable from owner `main` and a validation report bound to the exact commit.

It is a release-maintainer workflow, not the everyday installation path. See [Updates and rollback](../operator-guide.md#updates-and-rollback) for the commands and release safeguards.

Rollback is allowed only when the selected installed release supports the current database schema. Runtime data is never automatically rolled back or deleted.

## News Wire release history

### 0.4.2 - Relevance-only notifications

- Added immutable schema-9 notification events keyed by normalized canonical
  URL, with no historical backfill and no quality, importance, or priority gate.
- Added bridge protocol 2.2, encrypted owner-device Web Push subscriptions,
  individual FIFO delivery, durable 72-hour actions, and research-result
  follow-ups.
- Added a notification web app and two-control alert card. Full headlines and
  context can appear on the lock screen.
- Kept notifications in shadow mode until a private production canary and
  deliberate owner activation succeed.

### 0.4.1 - Owner-only online dashboard

- Added full hosted parity for overview, inbox, story and evidence detail,
  drafts and version history, exports, sources, schedule and usage, settings,
  diagnostics, filtering, sorting, and pagination.
- Added outbound-only bridge protocol v2 with normalized projections,
  on-demand historical reads, ten-second polling, replay protection, payload
  limits, version checks, idempotent commands, and five-minute expiry.
- Added reliable bridge LaunchAgent lifecycle reporting and restart behavior.
- Kept the local loopback dashboard as the emergency operator fallback.

### 0.4.0 - Fast, ungated content workflow

- Added explicit source trust classes and durable background and draft-refresh research attempts.
- Applied an Urgent review-score floor of 80 immediately for trusted sources and after every terminal research outcome for other sources.
- Replaced qualification and approval controls with one Create content action and an immediate editable shell.
- Added a separate fresh web-search-only source pass before packet-only Reddit writing.
- Restricted Broader AI News to privacy, security, and regulation.
- Migrated active signals, watches, and candidates to Ready while preserving inactive and historical records.
- Renewed the signed Codex identity pin and isolation/search canary for `codex-cli 0.146.0-alpha.9.2`.

### 0.3.8 - Bundled Codex identity refresh

- Renewed the exact release pin for the verified ChatGPT-bundled Codex 0.146.0-alpha.3.
- Preserved the existing approved path, Apple-signature, OpenAI team, sandbox, broker, and no-tools checks.
- Kept schema 7 and required a fresh 24-hour isolation attestation before assisted drafting resumes.

### 0.3.7 - Operational recovery

- Restored optional assisted drafting through a restricted local broker.
- Added a current release-bound assistance status and attestation.
- Kept drafting available through deterministic editable shells.
- Corrected resurfaced and unknown-date handling.
- Repaired the audited orphaned evidence proposal without deleting history.

### 0.3.6 - Reliability and approval safety

- Added schema 7 story, material, observation, and source revisions.
- Bound approvals to exact claim and source signatures.
- Prevented parser, metadata, engagement, and duplicate changes from creating material updates.
- Hardened public-source networking and short-link limits.
- Removed live ranking fallbacks to historical priority.
- Disabled assistance until a current isolation implementation could pass.

### 0.3.5 - Freshness-first review

- Replaced frozen priority with a dynamic 24-hour review score.
- Added Review Now, Newest first, Older Context, and All History.
- Separated impact, evidence, momentum, and freshness.
- Added HuggingNews JSON discovery and source-momentum snapshots.
- Prevented old stories from retaining live Breaking or Fresh placement.

### 0.3.4 - Manual candidate approval

- Added warned manual neutral and open-source-lens approval actions.
- Preserved failed automated gates instead of rewriting them as passed.
- Allowed immediate drafting from a human-selected active story.
- Kept internal override labels out of content exports.

### 0.3.3 - Natural source attribution

- Added hosting and original-publisher provenance for Reporting evidence.
- Counted independent evidence by confirmed original publisher.
- Replaced mechanical attribution phrases with named publications and safe Markdown links.
- Added citation-token validation and safe linked HTML export.

### 0.3.2 - Immediate draft generation

- Started draft work immediately after explicit approval.
- Fixed bundled Codex executable discovery under the scheduler environment.
- Added visible generation, retry, failure, and reapproval states.
- Preserved one durable idempotent request for recovery.

### 0.3.1 - Operational readiness

- Added dedicated public parsers and clearer source-health states.
- Added pilot readiness, notification canary, shadow validation, and activation watermarking.
- Fixed whole-Mac offline grouping, next-scan display, source-shaped exports, and GitHub release names.

### 0.3.0 - Local V1 foundation

- Introduced the local dashboard, SQLite corpus, source registry, evidence workflows, scheduler, recovery queue, drafting approvals, exports, diagnostics, and purge controls.
- Kept runtime data outside Git and publishing manual.

## What a release does not include

- Runtime news data or drafts.
- A hosted canonical corpus, inbound laptop listener, or cloud execution of
  collection, research, drafting, or editorial mutations.
- A backup or synchronization service.
- An OpenAI API key or paid fallback.
- Automatic notification activation.
- Automatic publication.

Inherited Last30Days release history remains preserved in the repository changelog for attribution. The News Wire application follows its separate 0.3.x line.
