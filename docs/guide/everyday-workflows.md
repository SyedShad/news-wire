# Everyday workflows

## Browse fetched articles

Open **News inbox**. Every relevant fetched article appears separately, newest fetched first. Search headlines, publishers, and excerpts or filter the coverage lane. Old and unknown publication dates remain visible. Repeat fetches update the existing article without changing its first-fetched order.

Use **Visit source** to read the original page. There are no trust scores, priority floors, machine approvals, or editorial review queues.

## Generate text or graphics

Open an article and choose **Generate content** or **Generate graphics**. Each task has its own model, ordered skills, and optional customization field. Paste a draft, specify an angle, or give design directions as needed. Select at least one skill, then press Generate; no separate approval is required.

The selected skills control their research and output workflow. Graphics do not require a text draft first. The selected model runs the workflow; a local renderer may create the actual image files.

Progress appears as queued, running, needs input, completed, failed, or cancelled. Answer a skill's questions within its task. Retry creates a new attempt while preserving earlier outputs. Edit text and save a revision, copy it, or download it. Preview graphics and download the artifacts.

Final publication remains manual. Generating does not authorize publishing, messaging, or unrelated account changes.

## Connect your ChatGPT plan

Run locally:

```bash
uv sync --extra dev
uv run open-source-ai-news-wire migrate
uv run open-source-ai-news-wire generation login
uv run open-source-ai-news-wire generation status
```

Complete the first-party browser sign-in and authorize ChatGPT plan usage. Credentials remain on this Mac. The application does not fall back to paid API credentials. Runtime skill and model catalogs refresh independently of article collection.

Some skills require unavailable capabilities. ChatGPT-plan integration currently does not support native image-generation or hosted connector tools; the photo-based OpenAGI graphics renderer runs locally. An unavailable model, tool, dependency, or account limit appears as a generation failure and never hides the fetched article.

## Operational notices

Open **Operational Review** for source-health, catch-up, recovery, and source-update notices. Its badge counts unread operational notices only. Marking these read does not acknowledge article alerts. Source updates preserve existing outputs and manual edits.

## Scheduling and recovery

```bash
uv run open-source-ai-news-wire schedule run-now
uv run open-source-ai-news-wire schedule pause
uv run open-source-ai-news-wire schedule resume
uv run open-source-ai-news-wire schedule status
```

Collection and generation require the Mac awake and connected. The hosted dashboard shows synchronized data when the Mac is offline; operational commands wait for a current connection. Existing informational article-alert delivery remains independent.
