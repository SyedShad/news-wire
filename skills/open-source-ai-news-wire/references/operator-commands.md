# Operator Commands

Use `open-source-ai-news-wire` below; from a source checkout, replace it with `.venv/bin/open-source-ai-news-wire`.

## Inspection

```bash
open-source-ai-news-wire status
open-source-ai-news-wire sources list
open-source-ai-news-wire sources health
open-source-ai-news-wire dashboard
open-source-ai-news-wire diagnostics export --output ~/Desktop/news-wire-diagnostics.json
```

## Collection and recovery

```bash
open-source-ai-news-wire scan --trigger manual
open-source-ai-news-wire schedule run-now
open-source-ai-news-wire catch-up --start 2026-07-01 --end 2026-07-14
```

## Scheduler

```bash
open-source-ai-news-wire schedule install
open-source-ai-news-wire schedule pause
open-source-ai-news-wire schedule resume
open-source-ai-news-wire schedule status
open-source-ai-news-wire schedule uninstall
```

## Sources

```bash
open-source-ai-news-wire sources enable SOURCE_ID
open-source-ai-news-wire sources disable SOURCE_ID
```

Local enablement changes do not rewrite the tracked canonical registry.

## Assistance and pilot

```bash
open-source-ai-news-wire assistance check-isolation
open-source-ai-news-wire assistance enable
open-source-ai-news-wire assistance disable
open-source-ai-news-wire assistance run-pending
open-source-ai-news-wire pilot start-shadow
open-source-ai-news-wire pilot status
open-source-ai-news-wire pilot notification-canary
open-source-ai-news-wire pilot extend-validation --auto-activate
open-source-ai-news-wire pilot readiness
open-source-ai-news-wire pilot activate-notifications --confirm-reviewed
open-source-ai-news-wire pilot stop-notifications
```

The completion pilot uses the additional 72-hour validation and fail-closed automatic activation. The manual activation command is retained for compatibility. Drafting remains human-approved in both modes.

## Data deletion

```bash
open-source-ai-news-wire purge preview --category raw_observations
open-source-ai-news-wire purge execute --plan-id PURGE_PLAN_ID
```

Protected categories require `--include-protected` during preview and separate deliberate human selection. Purge has no undo.

## Local application releases

```bash
open-source-ai-news-wire app install
open-source-ai-news-wire app list
open-source-ai-news-wire app rollback --release-id RELEASE_ID
open-source-ai-news-wire schedule uninstall
open-source-ai-news-wire app uninstall
```

Application uninstall leaves the runtime corpus untouched. Remove the schedule before removing the application.
