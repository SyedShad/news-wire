# Features and use cases

Release 0.4.2 adds owner-only review notifications for every new relevant source item while keeping content creation and publication deliberate and manual.

## Coverage

| Lane | What belongs there |
|---|---|
| Open Ecosystem News | Open-source, open-weight, source-available, open-governance, model-hub, and open development news |
| AGI Development | Research, evaluations, capabilities, safety work, and public claims directly related to AGI |
| Broader AI News | Only AI developments directly concerning privacy, security, or regulation |

Broader AI News excludes generic funding, acquisitions, chips, launches, research, and safety stories unless the specific development directly intersects privacy, security, or regulation.

## Fast trust-and-status workflow

Configured first-party organizations, governments, original research sources, and vetted publications are trusted. Their stories become Ready and Urgent immediately with a review-score floor of 80.

Aggregators, social and discovery feeds, and unknown publishers require research. The worker automatically performs a bounded search without asking the user, records up to three independent safe sources, and applies the same score floor after any terminal result. Search failures are visible but do not suppress the story.

The dashboard uses three working states:

- **Ready:** active and available for content creation;
- **Researching:** automatic background source search is running;
- **Content ready:** an editable Reddit draft exists.

Trust state, research history, the score-floor explanation, and source provenance remain visible for audit. The dashboard warning `Verify this yourself` stays out of content and exports.

## One content action

Every active story has exactly one **Create content** button. There are no qualification, evidence, importance, research, approval, lens, or manual-override gates.

The action immediately creates an editable shell, then starts a new source-search attempt distinct from background research. It accepts at most three current, relevant, independent public HTTPS pages, deduplicates existing publishers, and records accepted sources with `draft_search` provenance.

Timeouts, account limits, unsafe URLs, stale results, and insufficient matches do not block the shell or drafting from sources already stored.

## Reddit drafting

There is one generation mode: Reddit Post. The generated content includes:

- a concise factual title;
- a value-first casual body;
- natural Reddit Markdown source links;
- an open-ended engagement prompt;
- a suggested flair when one can be inferred; and
- the exact reminder `Verify rules before posting`.

No subreddit is assumed. The user can edit and version the draft, copy it, or export Markdown/HTML. Publishing remains manual and no Reddit credentials are added.

## Freshness and audit history

Breaking, Fresh, Updated, Newly surfaced, and Older continue to describe time, not trust. Only a changed normalized claim set creates a material update; rescans, formatting, engagement, and source metadata do not.

Source evidence, research attempts, revisions, corrections, and decision history remain durable. Archived, withdrawn, completed drafts, and old versions are preserved during migration.

## Sources, schedule, and recovery

Sources show trust class, family, role, health, last check, lag, cursor, failure streak, and local enablement. Safe public-HTTPS validation, DNS and redirect checks, byte limits, and loopback/private-network rejection remain enforced.

The scheduler runs at `:00` and `:30` while the Mac is awake. Automatic recovery covers up to 72 hours; explicit catch-up is available for older intervals. First-Public Time is preserved.

## Local security and data controls

The dashboard is loopback-only and one-time-link protected. Fresh discovery runs in a separately restricted Codex invocation that permits only web search. Final writing receives the stored packet and has all tools disabled.

Settings exposes local counts, redacted diagnostics, and preview-first purge controls. Runtime data stays outside the repository. The product has no built-in backup, hosted canonical corpus, or automatic publishing; Sites stores only the redacted owner projection and relay state described in the hosted dashboard guide.
