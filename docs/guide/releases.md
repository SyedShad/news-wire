# Releases

Open Source AI News Wire uses semantic application versions and explicit database schema versions. A release can change the application without changing the database schema.

## Current release

| Item | Value |
|---|---|
| Application | 0.3.7 |
| Database schema | 7 |
| Release tag | [`v0.3.7`](https://github.com/SyedShad/open-source-ai-news-wire/releases/tag/v0.3.7) |
| Validated source commit | `47c77c45dd726271920c588267aec9ceb8190e1a` |
| Distribution | Private source repository; no prebuilt binary attached |

Release 0.3.7 restores optional ChatGPT drafting while keeping the application usable when assistance cannot run. It also corrects how aggregator discovery dates affect freshness and repairs an orphaned evidence-work state without deleting its history.

Highlights:

- A short-lived local connection broker limits assisted drafting to reviewed ChatGPT/OpenAI endpoints.
- One current release-bound assistance check controls enablement, generation, dashboard status, and readiness.
- Every approval has an editable deterministic fallback shell.
- Aggregator time is treated as discovery time, not automatically as the linked article's original publication time.
- Unknown original dates appear as Newly surfaced and receive no freshness points.
- High-attention Discovery links can be inspected automatically, but human confirmation is still required for verification.
- Scheduler health is read from the actual user LaunchAgent.

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

The migration operates on the configured local data root. The application does not create a backup before migration. Read the release notes and confirm the intended schema before updating.

If the schedule was installed with the checkout's `.venv/bin/open-source-ai-news-wire` launcher, `uv sync --locked` updates that same environment and no schedule reinstall is normally required.

## Immutable maintainer installs

The application also supports immutable local releases with an atomic current-version switch. That path requires a clean commit reachable from private `main` and a validation report bound to the exact commit.

It is a release-maintainer workflow, not the everyday installation path. See [Updates and rollback](../operator-guide.md#updates-and-rollback) for the commands and release safeguards.

Rollback is allowed only when the selected installed release supports the current database schema. Runtime data is never automatically rolled back or deleted.

## News Wire release history

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
- A cloud service or hosted dashboard.
- A backup or synchronization service.
- An OpenAI API key or paid fallback.
- Automatic notification activation.
- Automatic publication.

Inherited Last30Days release history remains preserved in the repository changelog for attribution. The News Wire application follows its separate 0.3.x line.
