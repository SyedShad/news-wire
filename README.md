# Open Source AI News Wire

Open Source AI News Wire is a local-first monitor for fresh open-ecosystem, AGI, and broader AI developments. It collects public no-login sources, clusters and qualifies evidence deterministically, and presents the result in a secured local review dashboard.

Reporting is neutral by default. A separately labelled open-source lens is available only when a Strong or Moderate opportunity is supported and a human approves the editorial work. The system never publishes automatically.

## V1 capabilities

- Dated registry covering official AI organizations, open ecosystems, research, government and law, corporate filings, safety and security, independent reporting, aggregators, public newsletters, and accessible social signals.
- Incremental RSS/Atom, JSON, sitemap, GitHub-release, research-feed, and dedicated dated Anthropic, Meta, CISA, HuggingNews, and Mastodon adapters with bounded SSRF-resistant networking.
- Evidence-aware clustering, primary/independent-confirmation gates, evidence-first manual qualification with recorded importance overrides, candidate and watch lifecycles, correction handling, and 72-hour automatic recovery.
- Local SQLite and content-addressed evidence storage outside Git, with explicit migration, diagnostics, integrity, storage-pressure, and purge operations.
- User-level macOS LaunchAgent at minute `00` and `30`, plus login/load recovery. It runs while the Mac is locked and awake, but does not prevent sleep or change power policy.
- Packet-only Codex assistance behind a mandatory isolation canary and local usage budget. No API key or paid fallback is supported.
- Approval-only immediate drafting with durable recovery, visible generation and retry states, versioned evidence and guidance history, production-safe Markdown/HTML source exports, and native notices protected by a 72-hour fail-closed automatic activation gate and historical-alert watermark.

## Install locally

Python 3.12.13 and dependencies are provisioned through `uv`; runtime data defaults to `~/open-source-ai-news-wire-data`.

```bash
uv python install 3.12.13
uv sync --locked
uv run open-source-ai-news-wire migrate
uv run open-source-ai-news-wire app install --source-root "$PWD"
~/.local/bin/open-source-ai-news-wire schedule install \
  --launcher ~/.local/bin/open-source-ai-news-wire
~/.local/bin/open-source-ai-news-wire pilot start-shadow
```

Open the on-demand interface with:

```bash
~/.local/bin/open-source-ai-news-wire dashboard
```

The dashboard binds only to loopback, selects a random port, and requires its one-time process-local access link. See [docs/operator-guide.md](docs/operator-guide.md) for routine operation and recovery.

Release `0.3.3` uses schema version 4. Reporting qualification now follows the human-confirmed original publisher, so syndications of one report cannot masquerade as independent confirmation. Approved drafts use natural named attribution with safe, portable Markdown source links. Explicit draft approval still starts generation immediately; the half-hour worker remains a recovery fallback. After completing a real evidence-to-revised-draft workflow, run the notification canary and `pilot extend-validation --auto-activate`; `pilot readiness` reports every activation gate.

## Development and verification

```bash
uv sync --locked
uv run pytest tests/news_wire
uv run pytest --cov --cov-branch --cov-report=term-missing
```

Controlled live checks are intentionally separate from deterministic fixtures. Runtime content, credentials, live-source access, and Codex access never enter CI.

## Safety and data boundaries

- Runtime data must remain outside every Git worktree; the default is `~/open-source-ai-news-wire-data`.
- There is no backup, restore, cloud synchronization, destination integration, or automatic publishing feature.
- Scheduler or application uninstall leaves runtime data untouched.
- Sources requiring accounts, cookies, payment, paywall circumvention, or unstable access stay disabled.
- Assistance fails closed if its filesystem/private-network isolation canary fails; deterministic monitoring continues.
- Purge is preview-first and protected editorial/evidence categories require an additional explicit flag.

The full design and gates are in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). Shared terminology is in [CONTEXT.md](CONTEXT.md), and the security model is in [docs/security-threat-model.md](docs/security-threat-model.md).

## License

MIT. Existing upstream attribution is preserved in repository history and license notices. Inherited Last30Days components remain only where they are deliberately retained and tested.
