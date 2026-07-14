# Open Source AI News Wire

Shared language for a news-monitoring product that surfaces timely open-source, AGI, and adjacent AI developments for neutral reporting, with optional explicitly approved analysis through an open-source lens.

## Language

**Open Source AI News Wire**:
The canonical product and skill name. "Open Source" names its coverage commitment and optional editorial lens; it neither restricts monitoring to openly released systems nor makes advocacy the default, and it assumes no particular project, community, or publishing destination.
_Avoid_: News Radar

**Destination-Neutral**:
Content that assumes no particular project, organization, community, publishing platform, or destination-specific rules. Destination-specific adaptation is separate from the Wire's core output.
_Avoid_: Project-specific, community-specific, platform-specific

**Openness Class**:
The licensing and access character of an AI system or release: **open source**, **open-weight**, **source-available**, or **closed/proprietary**. Publicly downloadable weights are not automatically open source.
_Avoid_: Treating open source, open-weight, and source-available as synonyms

**Coverage Lane**:
The reason a development belongs in the Wire. Every **News Candidate** belongs to at least one of the three lanes below.
_Avoid_: Content bucket, source type

**Open Ecosystem News**:
A development directly concerning open-source, open-weight, or source-available AI models, software, datasets, infrastructure, research artifacts, licenses, governance, or organizations.
_Avoid_: Open news

**AGI Development**:
A development materially related to the pursuit, measurement, safety, governance, deployment, or public understanding of artificial general intelligence, regardless of its **Openness Class**.
_Avoid_: Using AGI as a synonym for all AI news

**Open-Source Lens Opportunity**:
A broader AI development whose consequences create a credible editorial question about how openness, transparency, distributed access, auditability, or open governance could change the outcome. It requires an **Open-Source Relevance Bridge**.
_Avoid_: Tangent content, forced open-source angle

**Open-Source Relevance Bridge**:
An evidence-backed explanation connecting a broader AI development to a specific way openness could materially change its outcome, including meaningful limitations or counterarguments.
_Avoid_: "Closed bad, open good", generic pro-open-source spin

**News Signal**:
Any newly observed public AI-related item awaiting qualification. A signal does not by itself imply accuracy, importance, or editorial value; a high-potential unverified signal may justify a **Watch Notice**.
_Avoid_: News Candidate, alert, verified story

**Primary Source**:
The original public artifact or statement from the entity directly responsible for, affected by, or formally deciding the reported development.
_Avoid_: Commentary, aggregation, a report that merely links to the original

**Monitoring Role**:
The function a particular source item performs while the Wire discovers and qualifies a signal: **Event Source**, **Reporting Source**, or **Discovery Source**. Roles are assigned from the item's actual contribution, not permanently from the publisher's identity.
_Avoid_: Publisher-wide trust label, Source Role

**Event Source**:
An original artifact documenting the event itself, such as an official release, repository change, paper, filing, regulatory record, or direct statement. A qualifying Event Source is normally the **Primary Source**.
_Avoid_: Article summarizing an original artifact, aggregator link

**Reporting Source**:
A source contributing original reporting, evidence, verification, analysis, or counterpoint. Its independent work may count as an **Independent Confirmation**; material it merely repeats may not.
_Avoid_: Assuming all journalism is independent confirmation

**Discovery Source**:
A source used to find possible developments or observe **Momentum Signal**, including aggregators, newsletters, link feeds, and social feeds. Repetition or linking alone contributes no **Candidate Evidence**; genuinely original work is evaluated separately as a Reporting Source.
_Avoid_: Verification source, primary evidence, automatic confirmation

**Scout Scan**:
The lightweight recurring collection pass over configured no-cost, machine-readable Event Sources and Discovery Sources. It detects new or materially changed **News Signals** without performing full article analysis on every collected item.
_Avoid_: Full enrichment, paid search sweep, repeated analysis of unchanged items

**Local Scan Schedule**:
The macOS user-level calendar trigger that starts a Scout Scan at minute 00 and minute 30 of every hour during an Awake Session, plus once after login or restart. It launches short-lived work and requires no continuously running scheduler owned by the Wire.
_Avoid_: Cron assumption, always-on daemon, screen-unlocked requirement

**Schedule Control**:
The human-facing lifecycle for the per-user macOS LaunchAgent: **Install**, **Run Now**, **Pause**, **Resume**, **Status**, and **Uninstall Schedule**. It requires no administrator privileges or manual property-list editing; configuration changes reload atomically.
_Avoid_: System daemon, manual launchctl workflow, schedule action that deletes data

**Paused Schedule**:
The explicit state in which scheduled triggers stop while code, Runtime Data Root, Source Cursors, and queued work remain intact. Resume begins with a Coverage Gap check before normal scheduling continues.
_Avoid_: Uninstalled system, reset progress, silent catch-up omission

**Schedule Status**:
The local operational view of the last and next trigger, current single-instance lock, queue depth, Source Health, most recent Conditional Codex Invocation, and any Degraded Run or Timeliness Degradation state.
_Avoid_: Last-run timestamp alone, hidden queue, success without source health

**Source Cursor**:
The durable per-source record of the last successfully observed position or time. A scheduled or catch-up run derives missing work from Source Cursors rather than from the number of scheduler events that occurred.
_Avoid_: Detection timestamp, global-only last run, advancing after failure

**Source Transaction**:
The independent processing unit that retrieves one source's new material, stores it idempotently, and advances that source's Source Cursor only after a successful commit. Failure rolls back that source alone.
_Avoid_: Whole-run transaction, advance-before-store, partial source commit

**Degraded Run**:
A scan in which at least one Source Transaction fails while other sources complete successfully. Successful cursors remain advanced, failed cursors remain unchanged for retry, and the run reports incomplete coverage explicitly.
_Avoid_: Total success, total failure, silent partial coverage

**Idempotent Processing**:
The guarantee that retrying the same source interval or item produces the same canonical state without duplicate Story Clusters, alerts, evidence relationships, or artifacts.
_Avoid_: Best-effort deduplication, retry-created alert, URL-only identity

**Coalesced Wake Run**:
The single scan initiated after one or more calendar triggers pass during sleep. It computes the actual Coverage Gap from Source Cursors instead of replaying every missed half-hour trigger.
_Avoid_: One run per missed interval, alert storm, freshness reset

**Single-Instance Scan**:
The rule that only one scheduled or catch-up scan may modify runtime state at a time. A trigger arriving during active work is folded into the existing scan or a single pending follow-up rather than creating overlap.
_Avoid_: Concurrent writers, duplicated enrichment, one queued job per trigger

**Run Budget**:
The 25-minute hard execution limit for one invocation of the Local Scan Schedule. Scout Scan work runs first; new enrichment stops before the deadline so completed transactions can commit and the single-instance lock can be released safely.
_Avoid_: Unbounded process, work until next trigger, forced termination during commit

**Enrichment Queue**:
The durable, idempotent queue of Signal Enrichment and catch-up units not yet completed within a Run Budget. Work resumes in later runs without repeating committed units or delaying the next Scout Scan.
_Avoid_: In-memory-only queue, duplicate retry, enrichment ahead of discovery

**Timeliness Degradation**:
The explicit operational state reported when the Enrichment Queue grows across successive runs faster than it can be processed. It describes delayed analysis or catch-up rather than pretending coverage remains current.
_Avoid_: Silent backlog, source outage, freshness relabeling

**Signal Enrichment**:
The deeper discovery, retrieval, clustering, and verification pass triggered by a novel or materially changed **News Signal**, or by a catch-up review. Optional rate-limited or paid sources may participate only when explicitly configured; they are not dependencies of the free local workflow.
_Avoid_: Every-scan requirement, automatic drafting, mandatory paid service

**Deterministic Core**:
The model-independent monitoring path for source collection, timestamps, source roles, fingerprints, Source Cursors, basic relevance rules, evidence gates, and durable state. It remains functional when ChatGPT is unavailable or its usage limit has been reached.
_Avoid_: LLM-required scan, generated verification, semantic-only filter

**ChatGPT Assistance**:
Remote semantic and editorial help performed by the non-interactive Codex CLI authenticated through the user's existing ChatGPT plan. It may assist classification, ambiguous clustering, summarization, Opportunity Strength, and approved drafting, but never counts as Candidate Evidence, a Primary Source, or an Independent Confirmation.
_Avoid_: OpenAI API key, local-model requirement, model as fact source

**Conditional Codex Invocation**:
The rule that a scheduled run invokes ChatGPT Assistance only when the Deterministic Core finds a novel or materially changed signal requiring semantic work, or when a human-approved drafting or revision task is pending. An empty scan never consumes a Codex invocation.
_Avoid_: One model call per schedule trigger, unconditional cron prompt, paid fallback

**ChatGPT Processing Packet**:
The minimum structured input permitted to leave the Mac for one ChatGPT Assistance task: source title, original URL, publication time, Source Role, relevant short passages, extracted claims and Evidence Map, necessary entity and classification metadata, the requested operation, and applicable human guidance. It excludes full archives, unrelated stories, credentials, and machine-specific paths.
_Avoid_: Runtime database access, complete article upload, whole-corpus context

**Remote Processing Guard**:
The isolation applied to every Conditional Codex Invocation: the Processing Packet enters through standard input; Codex runs ephemerally and read-only without Runtime Data Root access, write-capable tools, or API secrets; and only schema-conforming output may return. Ephemeral local execution does not override OpenAI account-level retention or training controls.
_Avoid_: Writable agent, repository-wide data access, promise of zero remote retention

**Validated Assistance Result**:
A schema-conforming ChatGPT response whose material factual claims remain mapped to supplied evidence and whose provenance records the model, prompt version, operation, and corresponding ChatGPT Usage Ledger entry. Malformed or unsupported output is rejected locally.
_Avoid_: Free-form trusted output, model-generated evidence, provenance-free draft

**ChatGPT Usage Ledger**:
The local record of each Conditional Codex Invocation, including purpose, model and prompt version, observable input and output size, result, and any usage-limit condition. It tracks plan consumption operationally without pretending that ChatGPT plan usage is metered as an API dollar charge.
_Avoid_: API Cost Ledger, invented token bill, unrecorded model run

**Background Usage Share**:
The conservative target that scheduled discovery, classification, and enrichment consume no more than 20% of the user's shared ChatGPT Pro agentic allowance. Human-approved drafting and revision are tracked separately and may use additional plan capacity. Because the plan does not expose a precise task-level percentage meter, the Wire enforces a configurable local invocation-and-effort proxy and calibrates it against observed plan usage.
_Avoid_: Guaranteed exact percentage, draft allowance, API token budget

**Codex Assistance Policy**:
The model-use policy for ChatGPT Assistance: use the account's supported default Codex model, record the actual model, version prompts and schemas, use low reasoning for routine triage, medium for candidate synthesis and normal drafting, and high only after an explicit human request. A transient or malformed result receives at most one retry before queueing and surfacing the condition.
_Avoid_: Unrecorded model change, unlimited retry, high reasoning by default

**Human Classification Needed**:
The visible state for a signal the Deterministic Core cannot classify confidently without ChatGPT Assistance. The item remains reviewable rather than being discarded or promoted through a guess.
_Avoid_: Rejected signal, automatic candidate, hidden low-confidence decision

**Source Registry**:
The maintained inventory of sources the Wire can monitor, organized across official AI organizations and researchers; model, code, data, package, and infrastructure ecosystems; research, conferences, benchmarks, and evaluations; government, legal, standards, and public records; safety, security, misuse, and incidents; compute, funding, and industry activity; and independent reporting, aggregators, newsletters, and public social signals. V1 begins with free, publicly accessible sources; canonical definitions are tracked while local enablement and overrides remain untracked.
_Avoid_: Single-aggregator coverage, untracked source list, claim of complete news coverage

**V1 Source Baseline**:
The free public starter registry covering official feeds and pages, GitHub releases, Hugging Face, arXiv, conferences and benchmarks, government and regulatory records, public filings, security advisories, HuggingNews, Hacker News, specialist reporting, general aggregators, newsletters, and public social signals. Account-, cookie-, paywall-, proxy-, and paid-dependent sources are disabled.
_Avoid_: Paid prerequisite, personal session, one-aggregator baseline

**Registry Change Approval**:
The explicit human acceptance required before a proposed source addition, removal, or durable definition change becomes canonical Tracked Configuration. Interface proposals and local enablement do not silently modify the registry in source control.
_Avoid_: Self-modifying registry, automatic commit, remote list as authority

**Targeted Multilingual Coverage**:
Global, source-led monitoring that combines English-language sources with original-language feeds for high-priority AI organizations, research groups, and government or regulatory bodies without a fixed language allowlist. V1 drafts in English; sensitive legal or technical claims require an official translation or additional confirmation before being stated as fact.
_Avoid_: English-only coverage, whole-web completeness claim, translation-only evidence

**Translation Provenance**:
The preserved relationship among original-language text, its original URL, the translation method, and a clearly labelled machine translation. Translation assists screening and drafting, while verification remains anchored to the original source.
_Avoid_: Unlabelled translation, translated text presented as original, discarded source language

**Source Health**:
The observable condition of a registered source, including whether it is reachable, timely, parseable, and successfully scanned. Source Health exposes degraded coverage; it does not assess the truth of individual items.
_Avoid_: Publisher credibility score, Candidate Evidence, silent failure

**Source Health Alert**:
An operational notice issued after three consecutive scheduled failures for one source, immediately for a non-retryable configuration, authentication, or parser failure, or immediately when a complete source family loses coverage. Individual-source alerts remain in the Review Inbox; family-wide loss also creates an Interrupting Alert.
_Avoid_: Candidate Alert, first transient-error notification, silent outage

**Coverage Recovery Notice**:
The single operational notice issued when a previously alerted source or source family becomes healthy again. It states whether recovery catch-up completed or whether a Coverage Gap remains.
_Avoid_: Notification per recovered item, implied complete catch-up, Candidate Alert

**External Service Budget**:
The maximum permitted incremental pay-as-you-go spending on non-local services beyond the user's existing ChatGPT plan. It defaults to zero; OpenAI API billing and other paid adapters remain disabled while the Deterministic Core and Conditional Codex Invocation path continue within available plan usage.
_Avoid_: ChatGPT subscription price, expected spend, soft warning

**Paid Adapter**:
An optional source or processing integration that can incur external charges. It remains disabled until explicitly activated with known pricing and a configured spending cap; reaching that cap disables only the adapter and visibly degrades its coverage.
_Avoid_: Automatic fallback, uncapped service, hidden dependency

**Cost Ledger**:
The record of estimated and actual pay-as-you-go external-service usage checked before and updated after each chargeable request. Unknown or untrackable pricing makes the associated Paid Adapter unavailable; ChatGPT plan invocations belong in the ChatGPT Usage Ledger instead.
_Avoid_: Display-only estimate, plan-usage estimate, unrecorded chargeable usage

**Manual Retention**:
The default local-data policy: collected records remain available until the user explicitly purges them. Age alone never triggers deletion, and no data category has a hard-set automatic expiry.
_Avoid_: Scheduled deletion, hidden cleanup, mandatory retention window

**Purge Recommendation**:
A non-destructive review that identifies data the user may choose to remove, grouped by category and accompanied by estimated space recovery and known effects on evidence, drafts, or audit history. A recommendation never performs deletion.
_Avoid_: Automatic purge, unexplained cleanup, selected-by-default deletion

**Confirmed Purge**:
An explicit human action selecting particular recommended or manually chosen local data for permanent removal after reviewing its consequences. High-value records such as drafts, decisions, Evidence Maps, and the Cost Ledger require separate deliberate selection.
_Avoid_: One-click erase-all, implicit consent, background deletion

**Local Data Store**:
The portable on-device persistence model: one SQLite database for structured records and relationships, plus a content-addressed file store for larger fetched material, translations, and generated artifacts. It requires neither a cloud database nor a continuously running service.
_Avoid_: Remote dependency, one loose file per record, duplicate stored content

**Local Runtime Boundary**:
The v1 storage and orchestration boundary in which collected news, evidence, editorial work, review state, and operational history remain stored on the Mac, while code and non-secret configuration may be synchronized to private source control and selected inputs may be transmitted to OpenAI for ChatGPT Assistance. "Local" does not mean local inference.
_Avoid_: Claim of fully local processing, hosted runtime database, remote corpus synchronization

**Source-Controlled Project**:
The code, tests, documentation, schemas, public source definitions, and safe configuration maintained in a **Private Derivative Repository**. It contains no credentials, machine-specific secrets, collected news, or editorial artifacts.
_Avoid_: Runtime database, fetched content, drafts, API keys

**Private Derivative Repository**:
The independent private GitHub repository for Open Source AI News Wire. It preserves the local project history and required license notices but is not registered as a GitHub fork of the public upstream, whose fork network is public.
_Avoid_: Private fork of a public fork network, rewritten attribution, runtime-data remote

**Upstream Remote**:
The read-only Git remote pointing to the original public project so selected upstream changes can be reviewed and merged into the Private Derivative Repository without sharing the private repository's runtime data or configuration secrets.
_Avoid_: Deployment remote, automatic upstream merge, runtime synchronization

**Tracked Configuration**:
Non-secret, portable settings suitable for the Source-Controlled Project, including defaults, source-registry definitions, scoring policy, schedule policy, schemas, and validation rules. Machine-specific paths, credentials, and sensitive overrides remain local and untracked.
_Avoid_: Secret-bearing environment file, personal path, runtime state

**Local Runtime Corpus**:
All news-related and machine-generated runtime data: the SQLite database, content store, fetched material, translations, alerts, briefs, drafts, Evidence Maps, Review Inbox state, logs, Cost Ledger, and ChatGPT Usage Ledger. It remains outside source control and is never synchronized or uploaded wholesale, although selected inputs leave the Mac for ChatGPT Assistance.
_Avoid_: Git-tracked artifact, corpus upload, remote synchronization

**Runtime Data Root**:
The single local directory containing the Local Runtime Corpus, defaulting to `~/open-source-ai-news-wire-data/`. It must be physically outside the Source-Controlled Project and every Git worktree; configuration points to it without copying, linking, or generating runtime artifacts inside the repository.
_Avoid_: Repository subdirectory, Git-tracked symlink, mixed code-and-data root

**Runtime Path Validation**:
The setup and startup check that rejects a Runtime Data Root inside a Git worktree, warns about recognized cloud-synchronized locations, confirms custom paths are writable, and keeps unbacked status visible. The Review Session provides a direct control to reveal the validated root in Finder.
_Avoid_: Unchecked custom path, silent cloud synchronization, hidden data location

**Repository Boundary Guard**:
The layered protection that prevents credentials or Local Runtime Corpus files from entering source control. It combines explicit ignore rules, staged-file validation, secret detection, and a startup refusal when the Runtime Data Root is inside a Git worktree.
_Avoid_: Gitignore alone, warning-only leak check, cleanup after push

**No-Backup Pilot**:
The accepted v1 risk posture in which Open Source AI News Wire provides no backup, restore, snapshot, backup-detection, or synchronization feature for the Local Runtime Corpus. Mac-level backup behavior is outside the product's scope; loss or failure of the Mac's storage may permanently remove the corpus.
_Avoid_: Disaster-recovery claim, hidden snapshot, backup configuration, automatic export

**Migration Contract**:
The versioned schemas and export definitions that preserve a future path from the Local Data Store to online infrastructure without operating or backing up the v1 corpus remotely.
_Avoid_: Current cloud deployment, continuous synchronization, promised one-click migration

**Workspace Layout**:
The stable, category-based organization of configuration, source registries, runtime state, collected material, translations, editorial artifacts, reports, exports, and logs. Each category has one clear purpose so similar files remain together and operational data is not mixed with human-facing content.
_Avoid_: Flat dumping ground, category overlap, organization by whichever process created a file

**Category-First Hierarchy**:
The rule that physical folders are organized first by function, then by artifact type and Stable Story ID. Organizations, people, topics, Coverage Lanes, and other many-to-many classifications remain indexed metadata rather than competing folder trees; a leading entity may still appear in a readable filename.
_Avoid_: Entity-first folders, duplicate copies across topics, one folder tree per classification

**Stable Story ID**:
The immutable identifier shared by every database record and file artifact belonging to one Story Cluster. Human-readable titles and slugs may change without breaking relationships.
_Avoid_: Headline as identifier, URL as identifier, renumbering after edits

**Artifact Naming**:
The predictable naming standard for recognizable local files. Human-maintained names use descriptive lowercase kebab-case; generated story artifacts include a UTC time, artifact type, Stable Story ID, short slug, and explicit version rather than ambiguous labels such as "final" or "latest."
_Avoid_: Spaces, unexplained abbreviations, local-time ambiguity, final-final naming

**Independent Confirmation**:
A credible source that supports a claim through its own reporting or evidence rather than repeating another report. Syndicated and derivative reports share one confirmation.
_Avoid_: Duplicate coverage, citation chain, repost

**Candidate Evidence**:
The support required for a **News Signal** to qualify as a **News Candidate**: one identifiable **Primary Source**, or two **Independent Confirmations** when a primary source is unavailable.
_Avoid_: Popularity, a single anonymous claim, one unofficial source

**Candidate Importance**:
The minimum substantive threshold a verified **News Signal** must meet to become a **News Candidate**: it fits at least one **Coverage Lane**, contains meaningfully new information rather than repetition or routine commentary, and could materially affect AI capabilities, access, safety, governance, economics, research, or public understanding. All three conditions are required.
_Avoid_: Verification alone, freshness alone, popularity, minor updates

**Coverage Gap**:
An interval after the last successful scan during which the Wire could not observe new signals. A gap describes missing observation time, not missing publication time.
_Avoid_: Downtime story, stale news

**Awake Session**:
A logged-in macOS user session while the computer itself is awake. The screen may be unlocked, locked, or turned off without stopping scheduled scans; actual system sleep, shutdown, or logout ends the Awake Session and creates a Coverage Gap.
_Avoid_: Screen-on requirement, treating lock as sleep, promise to run during suspension

**Passive Power Policy**:
The rule that the Wire may operate throughout an Awake Session, including while the screen is locked, but never prevents sleep or changes macOS power, battery, display, or lid behavior. Sleep pauses monitoring and delegates recovery to catch-up.
_Avoid_: Caffeinate mode, automatic power-setting change, promise of sleep-time execution

**Power-Neutral Execution**:
The rule that an Awake Session receives the same Scout Scan, verification, enrichment, download, and catch-up behavior whether the Mac is plugged in, running on battery, or using Low Power Mode. Only actual sleep pauses work.
_Avoid_: Battery threshold, low-power deferral, charger-only enrichment

**Resource Envelope**:
The fixed execution limits applied in every power state: no more than four simultaneous network requests, one active request per host unless explicitly permitted, and one ChatGPT Assistance task at a time. Work runs at background priority and follows published rate limits and Retry-After instructions.
_Avoid_: Power-dependent limits, unbounded concurrency, foreground-priority worker

**Document Fetch Policy**:
The rule that collection retrieves relevant documents and metadata only, never model weights, datasets, installers, or unrelated binaries. An individual fetched artifact is limited to 25 MB by default; larger material is skipped unless explicitly approved.
_Avoid_: Release-asset mirroring, recursive download, unbounded file fetch

**First-Public Time**:
The earliest verifiable timestamp at which the underlying development became publicly accessible in a traceable source. Later reposts and the Wire's detection time do not reset it.
_Avoid_: Detection time, alert time, repost time

**Freshness Band**:
A time-based label derived from **First-Public Time**: **Breaking** from 0–2 hours, **Fresh** after 2–6 hours, and **Catch-Up** after 6 hours.
_Avoid_: Importance score, engagement level

**Momentum Signal**:
The observed rate and spread of attention across independent sources. It may reprioritize a **News Candidate** or indicate increasing saturation, but it never establishes truth, importance, or eligibility.
_Avoid_: Verification, mandatory qualification gate, virality as importance

**Candidate Priority**:
An explainable 100-point attention score assigned after candidate qualification: material impact 40, time sensitivity 20, freshness 15, novelty 10, source significance 10, and Momentum Signal 5. **Urgent** requires at least 80 plus high impact and immediate relevance; **High** is 60–79; **Standard** is below 60. A human override is permitted only with a recorded reason. Priority orders attention but never changes candidate eligibility.
_Avoid_: Eligibility score, opaque ranking, popularity ranking

**Opportunity Strength**:
A separate **Strong**, **Moderate**, or **Weak** assessment based on Open-Source Relevance Bridge specificity, evidence quality, editorial usefulness, timing headroom, and counterargument quality. Strong and Moderate items may qualify as Content Opportunities; Weak items remain eligible only for neutral treatment. Opportunity Strength never changes Candidate Priority.
_Avoid_: Candidate importance, advocacy intensity, reason to force an angle

**Story Cluster**:
The canonical record for one underlying development across URLs, languages, reposts, syndicated reports, and later coverage. It preserves the earliest **First-Public Time**, collects all source relationships, and carries one continuous signal, watch, candidate, opportunity, and drafting lifecycle.
_Avoid_: One record per URL, duplicate alert, resetting freshness

**Material Update**:
New evidence that changes confirmed facts, verification or watch status, expected impact, **Candidate Priority**, **Openness Class**, or the **Open-Source Relevance Bridge** of a Story Cluster. Repetition, a new derivative article, or increased engagement alone is not material.
_Avoid_: Any new link, popularity spike, timestamp refresh

**Correction Alert**:
A notification that a Material Update changes or invalidates information in an earlier Candidate Alert or Wire Draft. It identifies the affected claims and evidence rather than silently replacing prior content.
_Avoid_: Duplicate alert, silent edit, unlabelled update

**Draft Status**:
The evidence relationship of a Wire Draft: **Current** when consistent with the latest qualified evidence, **Needs Review** after a relevant correction, **Superseded** after an approved revision replaces it, or **Withdrawn** when its central claim is disproven. Status changes preserve every version and its source history.
_Avoid_: Deletion, automatic rewrite, publication status

**Revision Approval**:
An explicit human decision allowing a corrected version of a Wire Draft to be prepared after a Correction Alert. It is separate from the earlier Candidate Approval.
_Avoid_: Automatic correction, implicit approval, silent replacement

**Contradicted Candidate**:
A previously qualified News Candidate whose central claim is later refuted by credible evidence. It is no longer draftable, triggers a Correction Alert where applicable, and remains in the audit record.
_Avoid_: Deleted candidate, unresolved dispute, ordinary clarification

**Catch-Up Review**:
The evaluation of items first published during a **Coverage Gap** after monitoring resumes. It restores awareness but does not make an older item fresh or breaking.
_Avoid_: Live scan, breaking alert

**Extended Catch-Up Review**:
A human-requested review of the portion of a **Coverage Gap** older than the standard automatic review. It remains retrospective and does not change any item's **Freshness Band**.
_Avoid_: Breaking scan, freshness reset

**News Candidate**:
A **News Signal** that has passed the Wire's verification and importance checks. It warrants a **Candidate Alert** and may support a **Neutral News Brief** after approval; only a **Content Opportunity** is eligible for the optional **Open-Source Lens Brief** mode.
_Avoid_: Draft, finished post, approved story

**Content Opportunity**:
A **News Candidate** with a defensible **Open-Source Relevance Bridge** and enough editorial value to support an **Open-Source Lens Brief**. It is eligible for that mode only after explicit **Candidate Approval**; it is not the eligibility gate for a **Neutral News Brief**.
_Avoid_: Every News Candidate, forced advocacy, requirement for neutral drafting

**Watch Notice**:
A low-priority notification that a **News Signal** has **Watch Potential** but remains unverified. It is not publishable, cannot receive **Candidate Approval**, and becomes a **News Candidate** only after meeting **Candidate Evidence**.
_Avoid_: Candidate Alert, breaking-news claim, permission to draft

**Watch Potential**:
The combination required for an unverified signal to justify a **Watch Notice**: it would be materially important if true, has a credible trace, and is timely enough to provide a preparation advantage. All three conditions are required.
_Avoid_: Virality, anonymous chatter, engagement alone, generic speculation

**Watch Outcome**:
The explicit resolution of a **Watch Notice**: **Promoted** when it becomes a **News Candidate**, **Contradicted** when credible evidence refutes it, or **Expired** when its permitted watch period ends without verification. Each notice receives exactly one outcome.
_Avoid_: Silently dismissed, unresolved forever, duplicate follow-up notice

**Candidate Alert**:
A notification that a verified **News Candidate** is worth attention. It states whether the candidate is also eligible for the optional **Open-Source Lens Brief** mode as a **Content Opportunity**.
_Avoid_: Watch Notice, automatic approval, published story

**Review Inbox**:
The persistent local queue containing every Watch Notice, Candidate Alert, Correction Alert, source record, scoring explanation, and human review state. It remains available between short-lived scans without requiring an always-running application.
_Avoid_: Notification history, continuous server, transient scan output

**Interrupting Alert**:
A native local notification reserved for Urgent or High Candidate Alerts, Watch Notices, and all Correction Alerts. Standard candidates remain visible in the Review Inbox without interrupting the user.
_Avoid_: Notification for every signal, approval request, proof of importance

**Catch-Up Summary**:
A single grouped local notification describing the results of a Catch-Up Review or Extended Catch-Up Review. Its underlying items remain individually reviewable in the Review Inbox with their original Freshness Bands.
_Avoid_: Alert storm, combined candidate, freshness reset

**Review Session**:
An on-demand **Local Web Interface** session for inspecting and acting on the Review Inbox. It starts when requested, stops explicitly or after inactivity, and remains independent from scheduled monitoring.
_Avoid_: Always-on dashboard, remote service, requirement for scheduled scanning

**Local Web Interface**:
The primary single-operator interface, served only from the Mac's loopback address on a dynamically selected port and opened in the default browser. Its server-rendered pages cover Overview, Review Inbox, Story and Evidence, Draft Editor and Version History, Sources and Health, Schedule and ChatGPT Usage, and Settings and Data. It provides an explicit Stop action and closes after 30 minutes without a browser heartbeat.
_Avoid_: Internet deployment, network-accessible server, CDN asset, analytics beacon

**Server-Rendered Interface**:
The v1 Python presentation architecture for the Local Web Interface: Flask and Jinja render navigable HTML pages and expose small action and status endpoints, with bundled vanilla JavaScript, lightweight status polling, and no Node.js or single-page-application build pipeline. It preserves a clean boundary that can support a richer frontend later.
_Avoid_: Client-heavy SPA, remote asset, frontend build service

**Interface Isolation**:
The protection that keeps the Local Web Interface private to the Mac: loopback-only binding, a one-time session token, same-origin state changes, CSRF protection, a restrictive content security policy, and sanitization of all collected source content. The interface receives controlled database access but never exposes the Runtime Data Root directly.
_Avoid_: LAN binding, unauthenticated mutation, rendered source HTML, directory browsing

**Review Action**:
An explicit human instruction from a Review Session: **Approve Neutral Brief**, **Approve Lens Brief**, **Approve with Guidance**, **Request More Evidence**, **Request Another Lens**, **Snooze**, or **Dismiss** where applicable; and **Continue Watching** or **Stop Watching** for a Watch Notice. Approve Lens Brief and Request Another Lens apply only to a Content Opportunity. Viewing or acknowledging an item is never a Review Action and never implies approval.
_Avoid_: Passive approval, automatic drafting, ambiguous acknowledgement

**Approval Snapshot**:
The fixed evidence, candidate state, selected **Draft Mode**, applicable Open-Source Relevance Bridge, and human guidance covered by Candidate Approval or Revision Approval. A Material Update before drafting invalidates the snapshot and returns the item for review.
_Avoid_: Open-ended approval, approval surviving changed evidence, silent scope change

**Wire Draft**:
A **Destination-Neutral** content draft created from a **News Candidate** only after **Candidate Approval**. Generation proceeds through **Pending**, **Generating**, and **Draft Ready**, after which a human may accept, edit, request revision, return it to research, archive, or withdraw it. Every edit or regeneration creates a preserved version.
_Avoid_: Candidate Alert, platform-specific post, published story

**Draft Mode**:
The human-approved editorial form of a Wire Draft: **Neutral News Brief** by default, or **Open-Source Lens Brief** only when explicitly selected for a Content Opportunity. The Wire never infers permission to add advocacy.
_Avoid_: Automatic lens, implicit advocacy, destination format

**Neutral News Brief**:
The default Wire Draft mode available to any approved News Candidate. It contains a Factual Headline, Draft Metadata, Factual Brief, and attributed sources, with no advocacy section.
_Avoid_: Open-Source Lens, opinion, undraftable general news

**Open-Source Lens Brief**:
The optional Wire Draft mode available only to an approved Content Opportunity. It preserves the complete neutral-news structure, then adds a separately labelled Open-Source Lens.
_Avoid_: Default draft, hidden advocacy, lens without explicit approval

**Wire Format**:
The standard compact order for every Wire Draft: a **Factual Headline**; **Draft Metadata**; a two-to-four-paragraph Factual Brief; an Open-Source Lens only in the explicitly approved Open-Source Lens Brief mode; then sources grouped by Source Role with original links and short attributed excerpts. A Neutral News Brief normally uses approximately 120–220 body words; an Open-Source Lens Brief normally uses approximately 250–400, excluding metadata and sources, with flexibility for unusual complexity.
_Avoid_: Destination-specific template, mixed fact and advocacy, unstructured essay

**Factual Headline**:
A self-contained, non-clickbait statement of the confirmed development, normally 12–24 words. It conveys the event without advocacy, unsupported certainty, or a call to action.
_Avoid_: Teaser, question headline, promotional claim, open-source argument

**Draft Metadata**:
The concise factual labels accompanying a Wire Draft: First-Public Time, Freshness Band, topics, entities, Coverage Lane, and Openness Class.
_Avoid_: Article body, editorial argument, hidden scoring

**Factual Brief**:
The compact, source-grounded portion of a **Wire Draft** that states what happened without advocacy. It normally uses two to four short paragraphs and approximately 120–220 words, while distinguishing confirmed facts from attributed claims.
_Avoid_: Opinion, promotional framing, unlabeled inference

**Open-Source Lens**:
The explicitly labelled, pro-open analytical portion included only in an approved **Open-Source Lens Brief**. It develops the **Open-Source Relevance Bridge** and its meaningful counterargument, remains separate from the **Factual Brief**, and normally uses approximately 80–150 words.
_Avoid_: Hidden advocacy, factual-news voice, forced conclusion

**Source Role**:
The relationship a cited source has to a story, such as **Primary**, **Confirmation**, **Analysis**, or **Counterpoint**. A role describes evidentiary function, not prestige.
_Avoid_: Unlabelled source list, ranking by popularity

**Source Entry**:
The reader-visible record for one cited source: its Source Role, original title, publisher, publication time, original link, and a short attributed excerpt when useful. Machine-translated material also carries Translation Provenance.
_Avoid_: Bare URL, copied article, unattributed excerpt

**Evidence Map**:
The internal relationship between every material factual claim in a Wire Draft and the Source Entries that support, qualify, or contradict it. It remains available during review without cluttering the visible brief; a material claim without mapped support blocks drafting.
_Avoid_: Reader-facing citation wall, inferred support, unsupported generated claim

**Candidate Approval**:
An explicit human decision allowing one **News Candidate** to proceed to drafting in a selected **Draft Mode**. Neutral News Brief is the default; Open-Source Lens Brief requires both Content Opportunity status and explicit selection. Detection, qualification, and viewing never imply approval.
_Avoid_: Automatic approval, implicit approval

**Announcement Attribution**:
The rule that an official source proves that an entity made or published a claim, but does not independently prove every capability, performance, safety, or comparative assertion inside it. Unvalidated assertions remain explicitly attributed until additional evidence supports them.
_Avoid_: Official claim as objective proof, unattributed benchmark claim, inaccessible sole evidence

**Pre-Draft Revalidation**:
The fresh check of volatile facts, source availability, corrections, and the current Story Cluster immediately before generation. A Material Update invalidates the Approval Snapshot; the passage of time updates freshness display but does not alone cancel approval.
_Avoid_: Draft from stale snapshot, automatic approval renewal, freshness reset

**Publication Boundary**:
The v1 prohibition on automatic publishing and destination integrations. Human-facing output is limited to Copy to Clipboard and user-initiated export of one brief or story as Markdown, self-contained HTML, or a JSON evidence bundle.
_Avoid_: Auto-post, publishing credential, whole-corpus export as backup

**Calibration Suggestion**:
A human-reviewable proposal derived from dismissal, reprioritization, lens, or revision reasons. Feedback is stored, but no source rule, scoring weight, or filter changes until the suggestion is explicitly approved.
_Avoid_: Silent self-tuning, automatic retraining, behavior drift

**Offline Run**:
A scheduled attempt during confirmed whole-Mac connectivity loss. It advances no Source Cursors, does not increment individual source failure streaks, and produces one recovery-and-catch-up notice when connectivity returns rather than a family-outage alert storm.
_Avoid_: One source error per adapter, cursor advance, duplicate recovery alerts

**Application Release**:
An explicitly installed, versioned build under the stable local application directory. The LaunchAgent calls a stable launcher that atomically selects the active tested release; application updates never pull, merge, or install themselves automatically.
_Avoid_: Git-checkout execution, mutable in-place install, automatic update

**Pilot Migration**:
An explicit-update-only, additive, forward-only, transactional, and non-destructive schema change. Scans and Review Sessions never migrate automatically; older code refuses a newer schema, and failure leaves the existing schema unchanged.
_Avoid_: Startup migration, column or table deletion, in-place content rewrite

**Concurrent Review**:
The ability to use the Local Web Interface during a scan through SQLite WAL, short write transactions, and durable action queues. A contended human action becomes visibly pending instead of failing or bypassing the Single-Instance Scan rule.
_Avoid_: Locked interface during scan, dropped action, competing long transaction

**Storage Pressure**:
The local health state triggered below 10 GB of free space and made critical below 2 GB. The Wire warns at the first threshold and stops fetching, enrichment, drafting, and substantive database writes at the critical threshold; it never deletes data automatically.
_Avoid_: Automatic cleanup, continue-until-corruption, hidden stopped state

**Evidence Retention Scope**:
The default permanent material retained under Manual Retention: source metadata, fingerprints, and short normalized evidence passages for accepted signals. Fuller snapshots are retained only for permissively licensed primary material, Watch Notices, News Candidates, or an explicit Keep action; raw feed transport and complete journalism articles are not stored by default.
_Avoid_: Whole-web archive, unbounded raw response storage, automatic expiry

**Local Access Security**:
The v1 single-operator security model: loopback session isolation, restrictive filesystem permissions, and macOS Keychain for credentials, with no application accounts or application-level database encryption.
_Avoid_: Shared account system, plaintext secret, encryption key prompt during unattended run

**Network Egress Boundary**:
The rule that scheduled work may contact only registered public source hosts and OpenAI. It blocks private and local network addresses, unsafe URL schemes, suspicious redirects, browser cookies, arbitrary proxies, and credential-bearing URLs.
_Avoid_: SSRF, personal-session scraping, unrestricted outbound request

**Local Diagnostics**:
Structured on-device operational events containing identifiers, cursor lag, queue age, timings, counts, failures, and ChatGPT usage state without secrets, article bodies, Processing Packets, or URL query strings. No telemetry or crash report leaves the Mac automatically.
_Avoid_: Remote telemetry, sensitive log, automatic diagnostic upload

**Repository Destination**:
The private independent GitHub repository `SyedShad/open-source-ai-news-wire`, with protected main, review for nontrivial changes, and public publishing surfaces disabled.
_Avoid_: Public fork, GitHub Pages, public release, runtime corpus in Git

**Repository CI Boundary**:
Validation and security automation that may inspect private source code but receives no credentials, Local Runtime Corpus, live-source calls, or ChatGPT calls. Hosted CI covers portable checks; LaunchAgent and authorized Codex smoke tests remain local.
_Avoid_: Secret-bearing CI, live production data, scheduled model call in CI

**Clean Application Package**:
The new `open_source_ai_news_wire` application and matching skill surface built within the preserved repository history. Vetted inherited utilities may be reused selectively, but the product does not extend the inherited monolithic command as its primary architecture.
_Avoid_: User-facing legacy name, wholesale monolith rename, hidden product dependency

**Legacy Import Boundary**:
The decision to start with a clean Wire database and Runtime Data Root. Existing databases and configuration are never imported automatically; a read-only explicit importer may be considered later only if proven useful.
_Avoid_: Startup import, shared database, silent configuration migration

**Upstream Review**:
The manual monthly review, plus immediate relevant security review, of the public Upstream Remote. Useful changes enter a dated review branch, are selectively integrated, tested, and reviewed before installation; nothing is auto-merged.
_Avoid_: Scheduled merge, automatic install, unreviewed vendor update

**Validation Gate**:
The evidence required before installation: offline unit and integration tests, synthetic fixtures, migration and idempotency checks, scheduler and catch-up tests, interface security tests, mocked Codex contracts, preservation of the existing 84% branch-coverage floor, at least 90% coverage for new scheduler, security, and evidence-critical modules, one local macOS end-to-end test, and one manually authorized Codex smoke test.
_Avoid_: Live-source-dependent CI, coverage regression, untested scheduler install

**Shadow Run**:
The first 72 hours of the pilot after fixture replay, during which the complete local system runs with results visible only in the Review Inbox and native content alerts disabled. It is used for evidence review and calibration, not silent publication.
_Avoid_: Immediate alert launch, automatic drafting, production claim

**Pilot Acceptance**:
The gate for enabling live notifications for the remainder of the seven-day pilot: zero API or Paid Adapter calls, zero secrets or runtime artifacts in Git, zero unapproved drafts, zero unsupported material claims in the validation sample, zero replay duplicates, correct partial-source recovery and 72-hour catch-up, a loopback-only secured interface, benchmark detection within 60 minutes during an Awake Session, and no unresolved critical or high-severity security findings.
_Avoid_: Subjective readiness alone, paid fallback, unresolved critical defect

## Example dialogue

**Editor**: "The Wire surfaced this model release as a News Candidate. Has it drafted the story?"

**Operator**: "No. It will create a draft only after you give Candidate Approval."

**Editor**: "Why did it include news from a closed AI lab?"

**Operator**: "It is labelled closed/proprietary and qualifies as an AGI Development and an Open-Source Lens Opportunity. We are not presenting the lab itself as open source."

**Editor**: "What makes the open-source angle credible?"

**Operator**: "Its Open-Source Relevance Bridge identifies the specific dependency that openness could reduce, the supporting evidence, and the limitation that open deployments can also fail."

**Editor**: "Will every AI item generate an alert?"

**Operator**: "No. Every observed item begins as a News Signal. Only signals that qualify as News Candidates warrant notification."

**Editor**: "The Wire was unavailable overnight. Are the stories it found after returning breaking news?"

**Operator**: "They belong to a Catch-Up Review. Their status still depends on when they were first published, not when the Wire found them."

**Editor**: "Will a scan run while the screen is locked?"

**Operator**: "Yes, during an Awake Session. Locking the screen does not stop the job, but system sleep does and creates a Coverage Gap for catch-up."

**Editor**: "Will the Wire keep the Mac awake so it can continue scanning?"

**Operator**: "No. Passive Power Policy leaves sleep decisions to macOS and recovers missed coverage after wake."

**Editor**: "Will Low Power Mode reduce monitoring or enrichment?"

**Operator**: "No. Power-Neutral Execution keeps behavior consistent across power states while Passive Power Policy still allows macOS to sleep normally."

**Editor**: "Will monitoring a model release download the released weights?"

**Operator**: "No. Document Fetch Policy retrieves only the relevant announcement and metadata, within the fixed Resource Envelope."

**Editor**: "Will scheduled monitoring stop if ChatGPT is unavailable or the plan limit is reached?"

**Operator**: "No. The Deterministic Core continues, affected work stays queued or becomes Human Classification Needed, and no API or paid model is invoked automatically."

**Editor**: "This announcement was detected 20 minutes ago but originally published eight hours ago. Is it Breaking?"

**Operator**: "No. Its First-Public Time places it in the Catch-Up freshness band."

**Editor**: "Can I recover the older portion of a long Coverage Gap too?"

**Operator**: "Yes, by requesting an Extended Catch-Up Review; the recovered items keep their original freshness bands."

**Editor**: "Three articles repeat the same anonymous leak. Is that three confirmations?"

**Operator**: "No. They share one citation chain, so they are neither Independent Confirmations nor sufficient Candidate Evidence."

**Editor**: "An aggregator surfaced the story first. Does that make it the source of record?"

**Operator**: "No. It acts as a Discovery Source for that item. The Wire follows it to the Event Source or evaluates genuinely independent Reporting Sources."

**Editor**: "Does every 30-minute run reread and analyze every article in full?"

**Operator**: "No. The Scout Scan detects new or changed signals cheaply. Signal Enrichment runs only when a signal warrants deeper work or a catch-up review requires it."

**Editor**: "Will sleep create one queued scan for every missed half hour?"

**Operator**: "No. The Local Scan Schedule starts one Coalesced Wake Run, which uses Source Cursors to recover the actual gap under the Single-Instance Scan rule."

**Editor**: "Does pausing or uninstalling the schedule delete collected news?"

**Operator**: "No. Schedule Control changes only the per-user trigger. A Paused Schedule preserves all state, and Schedule Status remains available."

**Editor**: "One source failed while the others completed. Is all progress lost?"

**Operator**: "No. The run is Degraded. Completed Source Transactions keep their cursors, the failed source retries from its previous cursor, and Idempotent Processing prevents duplicates."

**Editor**: "Will one temporary network error interrupt me?"

**Operator**: "No. A Source Health Alert normally waits for three consecutive failures, but non-retryable failures and complete source-family outages surface immediately. Recovery produces one Coverage Recovery Notice."

**Editor**: "What happens if enrichment cannot finish before the next half-hour scan?"

**Operator**: "The Run Budget preserves completed work and moves unfinished units into the Enrichment Queue. Persistent backlog becomes visible as Timeliness Degradation."

**Editor**: "Can one news aggregator guarantee complete AI coverage?"

**Operator**: "No. The Source Registry spans multiple source families, and Source Health reports where coverage is degraded or missing."

**Editor**: "A major announcement appeared only in its original language. Must the Wire wait for English coverage?"

**Operator**: "No. Targeted Multilingual Coverage can surface it directly. Translation Provenance keeps the original evidence and labels the machine translation used for review."

**Editor**: "A paid search service might find more sources. Will the Wire start using it automatically?"

**Operator**: "No. The External Service Budget defaults to zero. A Paid Adapter requires explicit activation, known pricing, and a cap recorded through the Cost Ledger."

**Editor**: "Will old sources, drafts, or logs disappear after a fixed period?"

**Operator**: "No. Manual Retention keeps them until you act. A Purge Recommendation can show reclaimable data, but removal requires a Confirmed Purge."

**Editor**: "How can I tell which local files belong together without opening them?"

**Operator**: "Workspace Layout groups files by category, while Artifact Naming and the Stable Story ID make each story and version recognizable."

**Editor**: "If the code is in a private GitHub repository, is the news corpus there too?"

**Operator**: "No. The Source-Controlled Project contains code and safe Tracked Configuration. The Local Runtime Corpus remains on the Mac under the Local Runtime Boundary and follows the explicitly unbacked No-Backup Pilot posture."

**Editor**: "Does local storage mean source content never leaves the Mac?"

**Operator**: "No. Selected inputs are transmitted to OpenAI during ChatGPT Assistance. The corpus remains stored locally and is not synchronized or uploaded wholesale."

**Editor**: "Can ChatGPT inspect the entire news database during a scheduled run?"

**Operator**: "No. Remote Processing Guard exposes only the task-specific ChatGPT Processing Packet, and only a Validated Assistance Result can return to the local workflow."

**Editor**: "What if a configuration mistake points the news database into the code repository?"

**Operator**: "The Repository Boundary Guard refuses to start because the Runtime Data Root may not be inside any Git worktree."

**Editor**: "Where is the news data by default?"

**Operator**: "The Runtime Data Root defaults to ~/open-source-ai-news-wire-data/. Runtime Path Validation keeps it outside source control and warns if a custom path appears cloud-synchronized."

**Editor**: "Is the private GitHub repository a formal fork of the public upstream?"

**Operator**: "No. It is an independent Private Derivative Repository with an Upstream Remote, preserving history and license attribution while keeping the project private."

**Editor**: "A story involves several organizations and policy topics. Which entity folder owns it?"

**Operator**: "None. Category-First Hierarchy stores one canonical artifact and indexes every relevant entity and topic as metadata."

**Editor**: "This verified article discusses AI, but it only repeats last week's announcement. Is it a News Candidate?"

**Operator**: "No. Candidate Evidence establishes support, not importance. Without meaningfully new and potentially consequential information, it fails Candidate Importance."

**Editor**: "A credible reporter says a major release may be imminent, but it is not confirmed. Is that a Candidate Alert?"

**Operator**: "No. It may qualify only for a Watch Notice. If it later meets Candidate Evidence, it can become a News Candidate and receive a Candidate Alert."

**Editor**: "The rumor is popular but has no attributable source or artifact. Does it have Watch Potential?"

**Operator**: "No. Engagement cannot replace a credible trace."

**Editor**: "An official release is five minutes old and has no visible engagement. Does it have to wait before qualifying?"

**Operator**: "No. Momentum Signal is only a prioritization input. If the release meets the evidence and importance checks, zero engagement does not prevent it from becoming a News Candidate."

**Editor**: "This governance decision has no draftable open-source angle, while a smaller product story has a strong one. Which receives attention first?"

**Operator**: "Candidate Priority and Opportunity Strength are separate. The governance decision can receive the higher attention tier and still support a Neutral News Brief, while only the product story is eligible for an Open-Source Lens Brief."

**Editor**: "Five new articles and a translated repost appeared after the first alert. Will I receive six alerts?"

**Operator**: "No. They join the existing Story Cluster. The Wire alerts again only for a Material Update, such as changed evidence or consequential new facts."

**Editor**: "Later evidence disproved a central claim in the draft. Will the Wire silently fix the text?"

**Operator**: "No. It sends a Correction Alert, marks the draft Withdrawn or Needs Review as appropriate, and requires Revision Approval before preparing a new version."

**Editor**: "Will returning after a long Coverage Gap flood the desktop with notifications?"

**Operator**: "No. Catch-up produces one Catch-Up Summary. Every recovered item remains available in the Review Inbox for an on-demand Review Session."

**Editor**: "Must the review interface remain open for scheduled monitoring to work?"

**Operator**: "No. The Local Web Interface exists only during a Review Session; scheduled scans continue independently, and Interface Isolation prevents network access."

**Editor**: "Does the local interface require a separate frontend build system?"

**Operator**: "No. Server-Rendered Interface keeps the v1 UI navigable and interactive with minimal packaged JavaScript and no SPA build pipeline."

**Editor**: "I opened a News Candidate but did not choose an action. Can the Wire draft it?"

**Operator**: "No. Viewing is not a Review Action. Drafting requires explicit approval and uses an Approval Snapshot that becomes invalid if the story materially changes first."

**Editor**: "What happened to yesterday's unverified notice?"

**Operator**: "Its Watch Outcome is Expired. If it had met Candidate Evidence it would be Promoted; if evidence had refuted it, it would be Contradicted."

**Editor**: "Does every approved draft add a pro-open argument?"

**Operator**: "No. Neutral News Brief is the default and contains no advocacy. Only an explicitly approved Open-Source Lens Brief adds a separately labelled Open-Source Lens after the neutral reporting."

**Editor**: "This AGI development is important, but it has no defensible open-source argument. Do we lose it?"

**Operator**: "No. It can remain a News Candidate, receive a Candidate Alert, and proceed to a Neutral News Brief after approval. It simply is not eligible for an Open-Source Lens Brief."

**Editor**: "The draft reads cleanly, but how can I verify one particular claim?"

**Operator**: "Its Evidence Map identifies the supporting Source Entries. The visible brief keeps those sources grouped by role instead of inserting a citation after every sentence."
