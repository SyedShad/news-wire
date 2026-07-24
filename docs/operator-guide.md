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

The review inbox opens to `Review Now`: unresolved signals, watches, and candidates whose First-Public Time or genuine material update is within 24 hours. It is ordered by current review score and limited to 25 stories per page. Use `Newest first` when chronological triage is more useful, `Older Context` for material outside the live window, and `All History` for audit work. Catch-up remains an ingestion label and never makes old news fresh.

Cards separate durable impact, current attention, and verification. A high-engagement item may read `High attention · verification pending`; source or account breadth never qualifies the evidence gate.

Release 0.3.8 retains schema 7 story, material, observation, and source revisions. A source/passage change may require draft reapproval without making old news current. Only a changed normalized claim set creates a material update. Aggregator timestamps record discovery, not original publication. An undated linked article is shown as `Newly surfaced · original date unknown`, receives zero freshness points, and cannot be Breaking or Fresh. Feed or page timestamps more than 15 minutes ahead are quarantined to a safe alternate or detection time and appear in diagnostics.

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

Tracked definitions update source metadata without overwriting local enablement or cursors. Disabled entries document access, parser, policy, or operator-exclusion gaps. `sources health` exposes the distinction between runtime health, `validated-unavailable`, and `excluded-by-operator`. Never add credentials or cookie-backed sources.

The HTTPS client validates every DNS result and pins each connection to one validated public address while preserving the registered hostname for TLS. On dual-stack hosts it prefers IPv4 and may try up to 12 validated addresses across the redirect chain, only for connection failures or connection timeouts. It never falls through after a peer mismatch, unsafe redirect, unsupported response, or other policy failure. Worker-deadline exhaustion becomes durable backlog and does not advance a source failure streak.

## Assistance and drafting

The story page separates qualification from drafting. For a discovery signal:

1. Use **Inspect linked source** or attach another public HTTPS source.
2. After the bounded fetch completes, confirm its Event, Reporting, or Discovery role and map it to the claims it directly supports, reports or attributes, contradicts, or contextualizes.
3. For Reporting evidence, confirm whether the hosted page is original, syndicated, or citing another publication. Confirm or correct the suggested original publisher; suggestions never qualify a story by themselves.
4. The normal evidence gate requires one confirmed first-party Event source or two independent original reporting publishers. Two hosts repeating the same original report count once. Manual approval never changes a failed gate into a passed gate.
5. When evidence passes, **Qualify as candidate**. If automated importance did not pass, record the human editorial reason; this changes neither the automated score nor the evidence result.
6. Separately approve and create a Neutral News Brief or, for a Strong or Moderate opportunity, an Open-Source Lens Brief. The dashboard dispatches that approved draft immediately and opens the editor when generation finishes in the active story tab.

When the normal drafting controls are locked, **Manually approve and create neutral draft** and **Manually approve and create open-source lens draft** provide an explicit human override. The browser asks for confirmation before submission. Confirming records the current evidence, importance, and lens states; marks the story as human-selected; and dispatches the selected draft immediately. No written reason is required. This path can override all three drafting gates, but it does not verify a source, change the automated score, or reclassify Discovery material as qualifying evidence. Archived, withdrawn, claim-less, and source-less stories remain ineligible.

For a manual override only, a stored safe public Discovery URL may be linked and named naturally in the draft. The internal **Manually approved** badge and failed-gate record remain in the dashboard and audit history; they are never included in copied content or Markdown, HTML, or content JSON exports. Publication remains a separate manual action.

Official pages prove that an announcement was made. Use **Reports or attributes the claim** for announcement or promotional claims unless the page directly establishes the factual claim. Excluding evidence preserves the audit record and invalidates qualification or pending drafts when the gate no longer passes.

Drafts name the confirmed publication naturally and link it on first mention. When the original URL is unavailable, the accessible copy is labelled in the form `Original Publisher, via Host`. Markdown exports use portable CommonMark links and HTML exports render only links already present in the confirmed evidence snapshot.

Deterministic monitoring works without ChatGPT assistance. Release 0.3.8 permits ChatGPT drafting only after the signed bundled Codex identity and the release-bound filesystem, child-credential, private-network, Unix-socket, broker, and no-tools canaries pass:

```bash
open-source-ai-news-wire assistance check-isolation
open-source-ai-news-wire assistance status
open-source-ai-news-wire assistance enable
```

The canary uses the saved ChatGPT login and a short-lived loopback CONNECT broker; no API key is supported. Its attestation expires after 24 hours and the worker may renew it at most once per rolling day within the background allowance. If it fails, assistance stays disabled and the exact condition appears in the dashboard and `assistance status`.

Every approval creates a deterministic editable draft shell when AI generation is unavailable, so editorial work does not wait for the next scan. The shell is built only from the immutable approval snapshot and contains no internal warning labels. Saving it completes the manual draft and cancels the pending AI work; if a valid AI result arrives first, it atomically supersedes the shell. A material claim or approved source-passage change still requires reapproval. A transient generation failure receives one automatic retry, then the same approval can be retried manually.

## Pilot activation

```bash
open-source-ai-news-wire pilot start-shadow
open-source-ai-news-wire pilot status
open-source-ai-news-wire pilot notification-canary
open-source-ai-news-wire pilot extend-validation
open-source-ai-news-wire pilot extend-validation --auto-activate
open-source-ai-news-wire pilot readiness
open-source-ai-news-wire pilot activate-notifications --confirm-reviewed
```

`pilot extend-validation` begins an unarmed 72-hour containment epoch in shadow mode. It preserves the original pilot history and cannot enable notifications when the window ends. Use `--auto-activate` only when automatic notification activation has been explicitly authorized; that armed path requires a successful notification canary and one real confirmed-evidence workflow through candidate qualification, human-approved neutral drafting, and a saved revision.

The worker activates notifications automatically only after every readiness gate passes: schema and database integrity, active scheduler, a healthy post-repair success from every enabled source, empty durable queue, safe disk state, assistance isolation, successful canary, and offline-alert suppression. Activation writes a watermark, so the historical inbox backlog is never delivered as native notifications. A failed gate leaves the system in shadow mode with explicit blockers. Drafting and publishing remain human-controlled.

`pilot activate-notifications --confirm-reviewed` remains available as a manual compatibility path, but the V1 completion pilot uses fail-closed automatic activation.

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

## Private GitHub safeguard

GitHub Free does not provide server-side branch protection for private repositories.
This checkout uses `.githooks/pre-push` as a local safeguard against deleting or
force-pushing `main`. Activate it in each trusted clone with:

```bash
git config core.hooksPath .githooks
```

The local guard can be bypassed and does not protect GitHub web or API changes.
Enable server-side branch protection if the repository later moves to an eligible plan.
