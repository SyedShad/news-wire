# Getting started

This guide installs Open Source AI News Wire from the private source repository, opens the local dashboard, runs the first scan, and optionally enables the half-hour schedule and ChatGPT drafting.

## What you need

- A Mac with access to this repository.
- Git.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/).
- Network access to the public sources you enable.
- ChatGPT with Codex access only if you want assisted drafting.

The application provisions Python 3.12.13 through `uv`. You do not need a separate OpenAI API key.

## 1. Get the source

```bash
git clone https://github.com/SyedShad/open-source-ai-news-wire.git
cd open-source-ai-news-wire
```

If you already have the repository, switch to `main` and update it instead:

```bash
git switch main
git pull --ff-only origin main
```

## 2. Prepare the application

```bash
uv python install 3.12.13
uv sync --locked
uv run open-source-ai-news-wire migrate
```

The migration creates or updates the local database. By default, runtime data lives at:

```text
~/open-source-ai-news-wire-data
```

Keep that directory outside the Git repository.

## 3. Open the dashboard

```bash
uv run open-source-ai-news-wire dashboard
```

The command opens a one-time local access link in the default browser. If the browser does not open, copy the printed access link. The server listens only on `127.0.0.1` and normally stops after 30 minutes without dashboard activity.

Use **Stop dashboard** in the sidebar when you are finished. Stopping the interface does not stop the scheduler or remove data.

## 4. Check the initial state

```bash
uv run open-source-ai-news-wire status
uv run open-source-ai-news-wire sources health
```

The status output should identify the runtime root, scheduler state, and current queue counts. Source health distinguishes healthy sources from degraded, unavailable, disabled, or operator-excluded sources.

One source failure does not mean the entire application is offline. Review the source family and its last successful check before changing anything.

## 5. Run the first scan

```bash
uv run open-source-ai-news-wire scan --trigger manual
```

You can also open the Overview and choose **Queue scout run**. A second trigger does not start a duplicate scan; it joins the durable work queue.

After the scan finishes:

1. Open **Review inbox**.
2. Keep the default **Review Now · 24 hours** window.
3. Confirm that current stories show freshness, review score, impact, attention, and verification separately.
4. Open a story to review its claims and sources.

## 6. Install the local schedule

This step is optional but recommended for regular monitoring.

```bash
uv run open-source-ai-news-wire schedule install \
  --launcher "$PWD/.venv/bin/open-source-ai-news-wire"
uv run open-source-ai-news-wire schedule status
```

The LaunchAgent runs at minute `00` and `30` while the Mac is awake. A locked screen does not stop it. Sleep, logout, shutdown, or a network outage pauses collection; the next eligible run recovers up to 72 hours automatically.

Useful controls:

```bash
uv run open-source-ai-news-wire schedule run-now
uv run open-source-ai-news-wire schedule pause
uv run open-source-ai-news-wire schedule resume
uv run open-source-ai-news-wire schedule status
```

Pausing the schedule does not stop an already running scan and does not remove collected data.

## 7. Enable ChatGPT drafting

This step is optional. Collection, review, evidence work, and manual draft editing work without it.

Make sure the ChatGPT application is installed, Codex access is available, and the account is signed in. Then run:

```bash
uv run open-source-ai-news-wire assistance check-isolation
uv run open-source-ai-news-wire assistance status
uv run open-source-ai-news-wire assistance enable
```

Enablement succeeds only when the current release-bound check passes. The check expires after 24 hours and may be renewed by the worker within its background allowance.

If assistance is unavailable, a human draft approval still creates an editable local shell immediately. The dashboard shows the specific waiting or failure condition.

## 8. Keep notifications in shadow mode

Native notifications are not required for normal use and should remain disabled until the readiness checks pass.

```bash
uv run open-source-ai-news-wire pilot start-shadow
uv run open-source-ai-news-wire pilot status
uv run open-source-ai-news-wire pilot readiness
```

Shadow mode keeps notices in the dashboard without delivering native content notifications. Drafting and publishing remain human-controlled in every pilot state.

## Try the interface with fictional data

Use a separate data directory so the demonstration cannot mix with the real corpus:

```bash
uv run open-source-ai-news-wire \
  --data-root /tmp/open-source-ai-news-wire-demo \
  init --demo
uv run open-source-ai-news-wire \
  --data-root /tmp/open-source-ai-news-wire-demo \
  dashboard
```

The dashboard displays a clear **Demo data** banner. Everything in that data root is fictional.

## Next steps

- [Understand the review and drafting features](features-and-use-cases.md)
- [Follow the everyday workflows](everyday-workflows.md)
- [Learn how updates and releases work](releases.md)
