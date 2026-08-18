# Everyday workflows

## Start a session

```bash
uv run open-source-ai-news-wire dashboard
```

Open **News inbox**. Use review priority for the most time-sensitive items, newest-first for chronology, Older Context for historical material, and All History for audit work. Filter by lane or state when useful.

## Understand a story

The story page shows the timeline, organic and review scores, source trust state, research status and history, claims, sources, provenance, and decision history.

- **Trusted source** means the configured publisher was reviewed as first-party, governmental, original research, or vetted reporting.
- **Researching** means the automatic 30-second source search is active.
- **Research complete** means the attempt reached a terminal state; partial or unavailable results remain visible.
- **Verify this yourself** is an editorial dashboard warning for a research-required detection. It is never copied into a Reddit draft or export.

No qualification action is required. Trusted stories receive the Urgent score floor immediately; research-required stories receive it after their automatic attempt ends.

## Create content

Select **Create content** on any active story.

The application immediately creates and opens an editable source-bound shell. It also records a new `draft_refresh` attempt, even if background research already ran. The fresh search looks for up to three current independent sources and adds only relevant, public, safely fetched HTTPS pages.

Search and writing are separate. A timeout, unsafe result, duplicate publisher, account limit, or no-result outcome stays visible but never removes the shell. Only a repeated click while the same content request is active is prevented.

## Edit the Reddit draft

Review these fields:

1. **Title:** concise and factual.
2. **Body:** value first, casual but accurate, and supported by attached sources.
3. **Suggested flair:** use it only if it fits the eventual community.
4. **Reminder:** keep the exact `Verify rules before posting`.
5. **Sources:** confirm each natural Markdown link supports the nearby statement.
6. **Engagement:** end with a genuine open-ended question.

The application does not assume a subreddit. Edit the shell or generated version as needed and save a new version. Use Copy, Markdown export, or HTML export only after checking the story and destination rules.

Final publication happens manually outside the application.

## Handle a changed story

A genuine claim change records a material update and may require the current content to be revised. Return to the story, inspect its updated sources and research history, then select **Create content** again after prior active work is complete. Every request receives a distinct fresh-search attempt and uses the latest story revision before writing.

Earlier drafts, sources, research attempts, corrections, and decisions stay in history.

## Archive or withdraw

Use Archive for material you want removed from active work but preserved in history. Use Withdraw when the story should no longer be treated as publishable. These are the only editorial state controls that intentionally make **Create content** unavailable.

## Check source health

```bash
uv run open-source-ai-news-wire sources health
uv run open-source-ai-news-wire sources disable SOURCE_ID
uv run open-source-ai-news-wire sources enable SOURCE_ID
```

Distinguish an individual degraded endpoint, a locally disabled source, a remote rate limit, and a whole-Mac outage. Existing stories and provenance remain stored when a source is disabled.

## Run now, pause, resume, or catch up

```bash
uv run open-source-ai-news-wire schedule run-now
uv run open-source-ai-news-wire schedule pause
uv run open-source-ai-news-wire schedule resume
uv run open-source-ai-news-wire schedule status
uv run open-source-ai-news-wire catch-up --start 2026-07-01 --end 2026-07-07
```

Overlapping scans coalesce. Pause stops future scheduled triggers; resume restores the `:00` and `:30` cadence. Catch-up preserves original publication time and uses the same trust and coverage rules.

## If assistance is unavailable

The editable shell remains usable. Check the visible condition with:

```bash
uv run open-source-ai-news-wire assistance status
uv run open-source-ai-news-wire assistance check-isolation
```

Do not bypass isolation, public-network, or account checks. You can finish the shell manually from stored safe sources, or restore assistance and let durable work recover. The local background allowance is informational and does not block automatic research.
