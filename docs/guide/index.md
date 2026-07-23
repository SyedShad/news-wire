# Product guide

Open Source AI News Wire is a local monitoring and editorial-review application for public AI news. It helps a person find current developments, see what evidence exists, decide what deserves coverage, and prepare a source-linked draft.

The application is designed for review, not automatic publication.

![Fictional overview showing the current workload, Priority Wire, source health, schedule, and notices](../assets/screenshots/0.3.7/01-overview.png)

*The overview uses fictional demo data. The Priority Wire contains current unresolved review work, while health and scheduling remain visible beside it.*

## The four product parts

### Public-source monitoring

The collector checks dated, public sources without using personal accounts or browser cookies. It supports official announcements, research feeds, GitHub releases, government and regulatory sources, independent reporting, aggregators, newsletters, and accessible public social signals.

The sources play different roles:

- **Event:** a first-party page associated with the event, release, filing, decision, or publication.
- **Reporting:** an identifiable publication reporting on the event.
- **Discovery:** an aggregator, link feed, newsletter, or public post that helps find a story but does not verify it by itself.

Related observations are grouped into one story so repeated links do not fill the review queue with duplicates.

### Local review dashboard

The dashboard organizes current work rather than showing a raw feed. It separates:

- **Impact:** the durable importance of the development.
- **Attention:** current source breadth and engagement momentum.
- **Verification:** whether the evidence requirements have passed.
- **Review score:** the current ordering signal, including freshness.

High attention does not mean verified. A popular story can remain labelled **verification pending** until a human confirms suitable evidence.

### macOS scheduler and recovery

The local scheduler runs at minute `00` and `30` while the Mac is awake. It continues while the screen is locked, does not prevent sleep, and does not change battery or power settings.

After sleep or offline time, normal recovery covers up to 72 hours. A person can request an older interval through Extended catch-up. Recovered stories keep their original publication time, so an old article does not become Breaking merely because it was found recently.

### Optional ChatGPT-assisted drafting

Drafting starts only after an explicit human approval. The selected claims, sources, mode, and guidance form an approval snapshot.

When assistance is available, the application asks the Codex client bundled with ChatGPT to create a structured draft from that snapshot. When it is unavailable, the same approval creates an editable local shell immediately. Monitoring and manual editing continue without ChatGPT.

## Editorial path

There are two ways to select a story for drafting.

### Evidence-qualified path

1. Inspect one or more public sources.
2. Confirm what each source is and which claims it supports, reports, contradicts, or contextualizes.
3. Qualify the story after the evidence requirements pass.
4. Approve either a neutral draft or an eligible open-source-lens draft.

Normal evidence qualification requires either:

- one confirmed first-party Event source; or
- two independent confirmed original Reporting publications.

Two websites repeating or citing the same original report count as one reporting origin.

### Manual selection path

When normal drafting is locked, the reviewer can choose **Manually approve and create**. The browser displays a warning before submission.

This is an editorial override, not verification. It leaves the evidence, importance, and lens results unchanged in the dashboard. The internal warning and override labels are not inserted into the draft or exports.

Archived, withdrawn, source-less, and claim-less stories cannot use the override.

## Drafting modes

### Neutral News Brief

The default mode produces a compact factual brief with natural source attribution. The first meaningful mention of a publication uses a portable Markdown link.

### Open-Source Lens Brief

This mode keeps the factual report and adds a separately labelled analysis section. The analysis should name a concrete open-source mechanism, such as auditability, access, transparency, distributed control, or open governance, and include a meaningful limitation or counterargument.

The normal path exposes this mode only for a Strong or Moderate opportunity. The warned manual path can override that eligibility decision.

## What stays local

- The dashboard binds only to the local Mac.
- Collected stories, evidence, decisions, and drafts live under the configured local data root.
- Runtime data stays outside the Git checkout.
- Stopping or uninstalling the dashboard does not remove the corpus.
- No backup, cloud synchronization, destination integration, or automatic publishing is included in V1.

## Where to go next

- [Install and launch the application](getting-started.md)
- [Browse all features and use cases](features-and-use-cases.md)
- [Follow the daily review and drafting workflows](everyday-workflows.md)
- [Review versions and updates](releases.md)
