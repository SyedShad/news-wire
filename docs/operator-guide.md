# Local operator guide

## Daily operation

The user LaunchAgent runs at minute `00` and `30` while the Mac is awake. A locked screen does not stop it. Sleep pauses execution; the next load or scheduled run recovers from the last successful scan, capped at 72 hours.

```bash
open-source-ai-news-wire status
open-source-ai-news-wire sources health
open-source-ai-news-wire schedule status
open-source-ai-news-wire dashboard
```

Use `schedule run-now` for an immediate bounded scan. Overlapping triggers coalesce into the durable queue.

Release 0.5.0 uses schema 10 and expands deterministic recognition of AI security advisories. The Overview and inbox show mutually exclusive Ready, Researching, and Content-ready states. Candidate, Watch, evidence, importance, lens, qualification, approval, and manual-override controls are retired. Archive and withdraw remain available.

## Relevance notifications

The dedicated notification lane captures each newly ingested source item only
when `qualification.relevant` is true. It does not consult quality, evidence,
importance, priority, popularity, or momentum. Normalized canonical URL is the
notification identity, so tracking variants and rescans notify once while
different URLs notify separately even when their titles or stories match.

Web Push is primary and is disabled by default. Start a 72-hour shadow review,
run the private production canary, and activate deliberately. An intentional
resume establishes a fresh watermark; ordinary laptop or bridge recovery keeps
eligible items for up to 72 hours. Full headlines and source context may appear
on the lock screen. Start working queues only `operator_review` source research
and sends a terminal follow-up; it never creates content or a draft. Dismiss
resolves only that notification.

## Private Telegram alerts

Telegram alerts are an optional local notification lane for one private team
group. They are informational: each message contains the relevant headline,
publisher/source context, reliable publication time, and public source link.
They never contain drafts, editorial controls, or hosted-dashboard actions.

Open BotFather and use `/newbot`, add the bot to the private group with group
privacy enabled, then confirm the destination from the Mac. Enter the token
hidden at the terminal; never paste it into Codex:

```bash
open-source-ai-news-wire telegram pair
open-source-ai-news-wire telegram send-test
open-source-ai-news-wire telegram enable
open-source-ai-news-wire telegram status
```

The token is stored through native macOS Keychain APIs. A short-lived pairing code
binds the command to the local destination confirmation. `send-test` must
verify a Telegram receipt before `enable` is accepted. The sender is restricted
to the paired private group ID and ordinary free Bot API delivery; paid broadcast
options, payment/Stars flows, public channels, and other groups are rejected.

The Telegram LaunchAgent runs at load and every 60 seconds while the Mac is
awake. Each dispatch has a 50-second budget, sends at most 12 items, and paces
them at four seconds per send. The existing scanner remains on its 30-minute
schedule. Delivery is independent of the hosted dashboard and continues from
the local ledger after restart. Disable or unpair with:

```bash
open-source-ai-news-wire telegram disable
open-source-ai-news-wire telegram unpair
open-source-ai-news-wire telegram dispatch
```

Only events with a reliable source publication time within 24 hours at send
time qualify. Unknown or future times, aggregator/discovery timestamps used as
fallbacks, and detection-time fallbacks are skipped. Enabling or resuming starts
at the current watermark and does not backfill older events. Sleep, logout, or
offline state stops delivery until the next awake launch. If a request may have reached
Telegram but its response is lost, the ledger retries under the bounded
at-least-once policy. A rare duplicate is possible and should be reconciled by
the operator.

## Trust, automatic research, and priority

Every configured source has one trust class:

- `trusted`: validated first-party organizations, governments, original research sources, and vetted reporting publications;
- `research_required`: aggregators, social or discovery feeds, and unknown publishers.

A trusted detection is Ready immediately and receives `review_score = max(organic_score, 80)`, shown as Urgent. A research-required detection starts background research in the same worker pass. The search has a 30-second total budget, seeks at most three current independent public HTTPS pages, and uses the existing safe-network boundary. Complete, partial, failed, and unavailable attempts all apply the same score floor so research availability never blocks coverage.

`Trusted source`, `Researching`, `Research complete`, and `Verify this yourself` are dashboard-only labels. Verification warnings must never enter copied or exported Reddit content. Research usage is still recorded; the local background allowance does not block source research. Remote account and rate-limit conditions remain visible.

## Coverage

Open Ecosystem News and AGI Development keep their existing scope. Broader AI News accepts a story only when it directly concerns one of these areas:

- privacy, personal data, surveillance, or data protection;
- cybersecurity, vulnerabilities, breaches, attacks, misuse, or security incidents;
- regulation, regulators, legislation, courts, compliance, copyright, or policy enforcement.

Generic funding, acquisitions, chips, launches, research, and safety stories do not belong in Broader AI News unless the development directly intersects one of those areas.

## Content creation

Every active story exposes one **Create content** action. It does not require qualification, approval, a lens choice, or manual confirmation.

Selecting it:

1. creates an editable, source-bound Reddit shell immediately;
2. records a distinct `draft_refresh` research attempt;
3. searches for at most three current independent publishers within 30 seconds;
4. deduplicates existing and syndicated publishers and attaches accepted pages with `draft_search` provenance; and
5. creates one Reddit draft from the latest stored story revision and source packet.

Timeouts, too few results, account limits, and safe-fetch rejection remain visible but do not remove the shell or block drafting from existing sources. Repeated clicks are blocked only while active content work already exists.

The Reddit draft has a concise factual title, value-first casual body, natural Markdown source links, an open-ended engagement prompt, suggested flair when inferable, and the exact reminder `Verify rules before posting`. It does not assume a subreddit. The user edits, copies, exports, and publishes manually; the application stores no Reddit credentials.

## Assistance isolation

Deterministic monitoring and editable shells work without ChatGPT assistance. Assisted fresh search and writing require the reviewed release-bound identity and isolation attestation:

```bash
open-source-ai-news-wire assistance check-isolation
open-source-ai-news-wire assistance status
open-source-ai-news-wire assistance enable
```

Release 0.5.0 retains the reviewed Apple-signed `codex-cli 0.146.0-alpha.9.2` pin. The canary verifies both execution paths: source discovery permits only secured web search, while final writing is packet-only with filesystem, shell, browser, apps, and all other tools disabled. The attestation expires after 24 hours. Failure keeps assistance disabled and records the exact safe error state.

## Hosted owner dashboard

The Sites frontend is an owner-only remote control and read projection. The
laptop remains canonical and accepts no inbound internet connection. Fresh
information and actions require the laptop to be awake and online; cached,
redacted information remains readable with its synchronization timestamp.

```bash
open-source-ai-news-wire hosted-bridge status
open-source-ai-news-wire hosted-bridge once
open-source-ai-news-wire hosted-bridge start
open-source-ai-news-wire hosted-bridge stop
```

`status` distinguishes not installed, installed, stopped, failed, and running.
`start` kick-starts a loaded but stopped service. Start and stop are deliberately
local-only; the hosted interface cannot stop its sole recovery connection.

The LaunchAgent polls every ten seconds, restarts after failure, and writes
redacted output under the runtime operations log directory. Owner commands
expire after five minutes and are rejected while the heartbeat is stale. To
recover, wake the laptop, confirm network access, run `hosted-bridge once`, then
start or reinstall the LaunchAgent. The local loopback dashboard remains the
emergency fallback.

Custom Sites access protects bridge APIs as well as browser pages. The laptop
therefore stores two independent credentials in macOS Keychain: the application
HMAC bridge secret and the Sites machine-access token. The latter is sent only
as `OAI-Sites-Authorization` to the configured HTTPS origin. Neither credential
is written to the bridge configuration or LaunchAgent plist. A production
`401` means the Sites token must be refreshed in Keychain before retrying.

## Sources and network safety

```bash
open-source-ai-news-wire sources list
open-source-ai-news-wire sources enable SOURCE_ID
open-source-ai-news-wire sources disable SOURCE_ID
open-source-ai-news-wire sources health
```

Tracked definitions update metadata and trust class without overwriting local enablement or cursors. Never add credentials or cookie-backed sources.

The HTTPS client validates every DNS result, pins each connection to a validated public address, preserves the hostname for TLS, rejects private and special networks, and revalidates redirects. It caps redirects, bytes, and time. A worker-deadline exhaustion becomes durable backlog rather than a source failure.

## Scheduling and recovery

```bash
open-source-ai-news-wire schedule install --launcher ~/.local/bin/open-source-ai-news-wire
open-source-ai-news-wire schedule run-now
open-source-ai-news-wire schedule pause
open-source-ai-news-wire schedule resume
open-source-ai-news-wire schedule uninstall
open-source-ai-news-wire catch-up --start 2026-07-01 --end 2026-07-07
```

Catch-up preserves First-Public Time and applies the same source trust and coverage rules. It does not make old news fresh. Uninstalling the schedule or application does not remove runtime data.

## Diagnostics and purge

```bash
open-source-ai-news-wire diagnostics export --output ~/Desktop/news-wire-diagnostics.json
open-source-ai-news-wire purge preview --category observations
open-source-ai-news-wire purge execute --plan-id PLAN_ID
```

Diagnostics redact local paths, tokens, and passages. Purge remains preview-first and has no undo. There is no built-in backup.

## Updates and rollback

```bash
open-source-ai-news-wire app install --source-root /path/to/open-source-ai-news-wire \
  --validation-report /path/to/commit-bound-validation.json
open-source-ai-news-wire app list
open-source-ai-news-wire app rollback --release-id RELEASE_ID
```

Pause the scheduler and snapshot the database before migrating. Each immutable install atomically switches the stable launcher. Rollback is allowed only when the selected release supports the current schema. After installing 0.4.3, run a fresh isolation/search canary before re-enabling assistance and resuming scheduling. Keep relevance notifications in shadow mode until the separate Web Push canary passes. A Sites rollback does not roll back the laptop database; the projection migrations are additive and harmless to the previous hosted shell.

## Private GitHub safeguard

GitHub Free does not provide server-side branch protection for private repositories. This checkout uses `.githooks/pre-push` as a local guard against deleting or force-pushing `main`:

```bash
git config core.hooksPath .githooks
```

The local guard can be bypassed. Enable server-side protection if the repository later moves to an eligible plan.
