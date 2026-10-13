# Product guide

Open Source AI News Wire monitors relevant public AI news and lets the owner prepare content and graphics through selected Codex skills. The canonical database, credentials, and generation run on the Mac. Publication remains manual.

## News inbox

Every distinct relevant article appears as **Fetched**, with its headline, publisher, excerpt, publication date when known, first fetch date, and source link. Different publishers' articles stay separate. Repeated fetches update an article without moving its original place in the feed. All fetched history is available by default, including old and undated articles.

Topic relevance filtering remains. Content grading, trust floors, automatic editorial research, and machine approval prerequisites are retired. Historical records and drafts remain available.

## Operational Review

Health, recovery, catch-up, and source-update notices have a separate tab and unread badge. Acknowledging them affects only operational notices. The existing external article-alert policy remains; operational notices add no external alerts.

## Article workspace

Each article has **Generate content** and **Generate graphics**. Choose a model and one or more ordered skills separately for each task, then add optional directions or a pasted draft. The runtime supplies both catalogs. Skills start unselected; later selections are remembered separately for the two tasks.

Tasks show queued, running, needs input, completed, failed, or cancelled status. Questions appear inside the task. Retry creates a new output version; saving edited text preserves the earlier version. Graphics can run without a text draft.

## Authentication and capabilities

Generation uses the documented local/open-source **Sign in with ChatGPT** route. Setup happens on the Mac; tokens never enter the hosted projection. Model presence in the catalog does not guarantee account access. Access failures are shown without changing models.

This route currently lacks native image generation and hosted connectors. The installed `openagi-graphics` skill can use its local renderer. Skills that require unsupported tools must report the limitation; no paid fallback is enabled. Hosted generation must remain unready until authentication and real command/result runs have been validated.

## Sources and security

Coverage remains Open Ecosystem News, AGI Development, and Broader AI News concerning privacy, security, or regulation. Source-fetch protections, local dashboard access controls, and the authenticated outbound hosted bridge remain in place.

- [Install and launch](getting-started.md)
- [Features and use cases](features-and-use-cases.md)
- [Everyday workflows](everyday-workflows.md)
- [Versions and updates](releases.md)
