# Local operator guide

## Daily operation

The user LaunchAgent runs at minute `00` and `30` while the Mac is awake. A locked screen does not stop it. Sleep pauses execution; the next load or scheduled run recovers from the last successful scan, capped at 72 hours.

```bash
open-source-ai-news-wire status
open-source-ai-news-wire sources health
open-source-ai-news-wire schedule status
open-source-ai-news-wire dashboard
```

Use `schedule run-now` for an immediate bounded scan. A second trigger coalesces into the durable queue instead of starting another worker.

## Scheduling

```bash
open-source-ai-news-wire schedule install --launcher ~/.local/bin/open-source-ai-news-wire
open-source-ai-news-wire schedule run-now
open-source-ai-news-wire schedule pause
open-source-ai-news-wire schedule resume
open-source-ai-news-wire schedule uninstall
```

Uninstalling the schedule does not remove the application or data. Uninstall the schedule before `app uninstall`; application uninstall also leaves runtime data intact.

## Missed time and recovery

Automatic recovery covers at most the preceding 72 hours. For an older explicit interval:

```bash
open-source-ai-news-wire catch-up --start 2026-07-01 --end 2026-07-07
```

Catch-up is inclusive, uses the same evidence gates, and groups burst notifications. It does not bypass source retention or availability limits.

## Sources

```bash
open-source-ai-news-wire sources list
open-source-ai-news-wire sources enable SOURCE_ID
open-source-ai-news-wire sources disable SOURCE_ID
open-source-ai-news-wire sources health
```

Tracked definitions update source metadata without overwriting local enablement or cursors. Disabled entries document access, parser, or policy gaps. Never add credentials or cookie-backed sources.

## Assistance and drafting

Deterministic monitoring works without ChatGPT assistance. Before enabling assistance, run:

```bash
open-source-ai-news-wire assistance check-isolation
open-source-ai-news-wire assistance enable
```

If the canary fails, leave assistance disabled and use the dashboard's human-classification states. Drafts are generated only from an approved work item; a material source change invalidates the approval snapshot.

## Pilot activation

```bash
open-source-ai-news-wire pilot start-shadow
open-source-ai-news-wire pilot status
open-source-ai-news-wire pilot activate-notifications --confirm-reviewed
```

Activation is unavailable until 72 elapsed hours, explicit human review, and a clean critical diagnostic gate. Drafting remains approval-only after notifications are enabled.

## Diagnostics and purge

```bash
open-source-ai-news-wire diagnostics export --output ~/Desktop/news-wire-diagnostics.json
open-source-ai-news-wire purge preview --category observations
open-source-ai-news-wire purge execute --plan-id PLAN_ID
```

Diagnostics redact local paths, tokens, and content passages. Purge plans are immutable and preview-first. Protected evidence or editorial categories require `--include-protected` at preview time. There is no automatic retention deadline and no backup feature.

## Updates and rollback

```bash
open-source-ai-news-wire app install --source-root /path/to/open-source-ai-news-wire
open-source-ai-news-wire app list
open-source-ai-news-wire app rollback --release-id RELEASE_ID
```

Each install builds an immutable pinned environment and atomically switches the stable launcher. Rollback is allowed only when the installed release supports the current database schema.
