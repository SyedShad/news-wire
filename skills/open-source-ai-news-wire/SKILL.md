---
name: open-source-ai-news-wire
description: Operate and inspect the local Open Source AI News Wire for fresh open-ecosystem, broader AI, and AGI developments. Use when Codex needs to check monitoring status, run or recover scans, inspect source health and evidence, open the local review dashboard, manage the macOS schedule, perform explicit catch-up or purge workflows, or assist with human-approved neutral and open-source-lens drafts.
---

# Open Source AI News Wire

Operate the local, destination-neutral AI news-monitoring and editorial-review system. Treat the SQLite store and dashboard as the source of truth; do not substitute a general web search for a configured scan.

## Start safely

1. Resolve the installed command with `command -v open-source-ai-news-wire`. In a source checkout, use `.venv/bin/open-source-ai-news-wire`.
2. Run `open-source-ai-news-wire status` before changing state.
3. Read [references/operator-commands.md](references/operator-commands.md) for the exact requested lifecycle operation.
4. Read [references/editorial-policy.md](references/editorial-policy.md) before reviewing evidence or drafting.

## Choose the workflow

- For current coverage, source health, queue state, or usage: inspect `status`, `sources health`, or launch `dashboard`.
- For a signal the human wants to select: inspect its linked public page or attach a public HTTPS source, confirm the source role and claim relationships, then qualify it. Evidence still requires one first-party Event source or two independent Reporting publishers; only automated importance may be overridden, with a recorded reason.
- For fresh retrieval: use `schedule run-now` or `scan --trigger manual`. Do not create parallel scans; the worker coalesces overlap.
- For missed awake/offline time: let scheduled recovery apply the default 72-hour cap. Use `catch-up --start YYYY-MM-DD --end YYYY-MM-DD` only when the user selects an older interval.
- For schedule lifecycle: use `schedule install|pause|resume|status|uninstall`. Explain that locked-and-awake works; sleep, logout, shutdown, and offline time pause work.
- For ChatGPT assistance: run the isolation check before enabling it. If isolation fails, keep assistance disabled and continue deterministic monitoring.
- For drafting: require an explicit human approval already recorded in the Wire. Never infer approval from viewing, discussing, or accepting a story.
- For deletion: create a purge preview first. Execute only the exact returned plan ID after explicit human confirmation; there is no backup or undo.

## Preserve boundaries

- Keep runtime data under the configured local data root and outside every Git worktree.
- Never add an OpenAI API key, paid fallback, publishing credential, browser cookie, proxy, or destination integration.
- Never publish automatically.
- Keep reporting neutral unless a Strong or Moderate open-source opportunity has a specific mechanism, evidence, counterargument, and explicit lens approval.
- Treat discovery feeds and popularity as signals, not verification.
- Never use manual qualification to bypass the evidence gate. Qualification and draft approval are separate review actions.
- Preserve First-Public Time during catch-up; do not relabel older material as breaking.
- Report source gaps, deferred work, isolation failures, and low-disk states plainly.

## Return useful results

Lead with operational state: last scan, next scan, queue, source failures, and candidate/watch counts. For editorial review, distinguish verified facts, attributed claims, uncertainty, and the optional separately labelled open-source lens. Include source links from the stored evidence bundle and avoid unsupported additions.
