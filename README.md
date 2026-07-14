# Open Source AI News Wire

Open Source AI News Wire is a local-first AI news-monitoring and editorial review project. Its working V0.1 dashboard presents story clusters, evidence, source health, human decisions, draft history, usage, and local-data controls through a secured on-demand web interface.

The V1 product is destination-neutral. It reports news neutrally by default and exposes an optional, separately labelled open-source lens only after human approval.

## Dashboard milestone

The dashboard milestone is implemented and verified. It includes all seven planned views, a local SQLite store, clearly labelled fictional demo data, guarded approval and editing flows, exports, staged schedule controls, manual retention controls, and responsive desktop/mobile layouts.

The live collector, source adapters, deterministic qualification engine, ChatGPT worker, macOS LaunchAgent, native notifications, and production installer are not installed by this milestone. Schedule actions currently create durable local queue records; they do not claim that a background worker is running. The complete phased design remains in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), and shared terminology is in [CONTEXT.md](CONTEXT.md).

## Development

Python 3.12 or later is required.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/open-source-ai-news-wire init --demo
.venv/bin/open-source-ai-news-wire dashboard
```

Runtime data defaults to `~/open-source-ai-news-wire-data/`. For development, set `OPEN_SOURCE_AI_NEWS_WIRE_DATA` to a directory outside the repository.

`init --demo` adds only fictional records and labels every dashboard page accordingly. Omit `--demo` to initialize an empty local store.

Run the focused dashboard suite with:

```bash
.venv/bin/pytest tests/news_wire -q
```

## Safety boundaries

- The dashboard binds only to loopback.
- Each launch uses a random port and a one-time process-local access link.
- Runtime data must remain outside Git worktrees.
- There is no automatic publishing.
- Every draft requires explicit human approval.
- V1 has no product backup, restore, or synchronization feature.
- OpenAI API and paid fallbacks are disabled.

## License

MIT. Existing upstream attribution is preserved in the repository history and license notices.
