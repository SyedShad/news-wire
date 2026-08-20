# Product guide

Open Source AI News Wire is a local-first monitoring and content-preparation application for public AI news. Release 0.4.3 expands deterministic recognition of AI security advisories while keeping the canonical corpus and operational backend on the laptop and publication in human hands.

## Public-source monitoring

The collector checks dated public sources without personal accounts or browser cookies. Related observations are grouped into stories. Every configured source is explicitly classified as trusted or research-required.

Trusted first-party, government, original-research, and vetted reporting sources make a story Ready and Urgent immediately. Aggregators, social feeds, discovery feeds, and unknown publishers trigger automatic bounded research; the story receives the same Urgent floor when that attempt ends, even if results are partial or unavailable.

## Focused coverage

The product has three lanes:

- Open Ecosystem News;
- AGI Development; and
- Broader AI News, restricted to privacy, security, and regulation.

Generic corporate, hardware, launch, research, and safety news is excluded from Broader AI News unless it directly intersects one of those three areas.

## Local dashboard

The dashboard organizes active stories into Ready, Researching, and Content-ready states. Each story shows trust status, background and draft-search history, score-floor reasoning, source provenance, claims, revisions, and decision history.

There are no editorial qualification, evidence, importance, research, approval, lens, or manual-override gates. Archived and withdrawn items remain inactive, unsafe network locations remain rejected, and duplicate active work remains prevented.

## One Reddit content workflow

Every active story has one **Create content** button. It creates an editable shell immediately and runs a distinct fresh search for up to three relevant independent publishers. Search failures do not block the shell or generation from existing sources.

The sole content mode follows the `reddit-posts` rules: concise factual title, value-first casual body, Reddit Markdown, natural source links, an open-ended engagement prompt, suggested flair when inferable, and `Verify rules before posting`. It does not assume a subreddit.

The user reviews, edits, copies, exports, and publishes manually. The application has no Reddit publishing integration or credentials.

## Scheduler, recovery, and security

The macOS scheduler runs at minute `00` and `30` while the Mac is awake and recovers up to 72 hours after missed time. First-Public Time is preserved.

The dashboard binds only to loopback. Public pages pass DNS, redirect, HTTPS, byte, and private-network checks. Fresh source discovery runs through a dedicated Codex invocation with web search as its only permitted tool. Final writing is packet-only with all tools disabled.

Runtime data stays outside Git. No backup, cloud synchronization, hosted corpus, destination integration, or automatic publishing is included.

## Where to go next

- [Install and launch the application](getting-started.md)
- [Browse features and use cases](features-and-use-cases.md)
- [Follow everyday workflows](everyday-workflows.md)
- [Review versions and updates](releases.md)
