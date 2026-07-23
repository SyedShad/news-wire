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
- For editorial triage: use the default 24-hour `Review Now` queue. Breaking means at most two hours, Fresh means over two through 24 hours, and an older story re-enters only as Updated after a genuine material development. Use `Newest first` when chronology matters; use `Older Context` or `All History` deliberately.
- For a signal the human wants to verify normally: inspect its linked public page or attach a public HTTPS source, confirm the source role and claim relationships, then qualify it. Reporting evidence also requires a human-confirmed original publisher and provenance type; syndications or citations of one origin count once. Normal qualification requires one first-party Event source or two independent original reporting publishers; only automated importance may be overridden, with a recorded reason.
- For a signal the human explicitly chooses despite locked gates: use the dashboard's **Manually approve and create** control for the selected mode. The native confirmation records a manual override of evidence, importance, and lens eligibility without changing those automated results. Do not describe this as verification. Archived, withdrawn, claim-less, or source-less stories cannot use it.
- For fresh retrieval: use `schedule run-now` or `scan --trigger manual`. Do not create parallel scans; the worker coalesces overlap.
- For source failures: distinguish a whole-Mac outage, a temporary rate limit, and an individual endpoint failure. The installed client may retry other already-validated public addresses, but it must retain hostname, certificate, exact-peer, redirect, and private-network checks. A worker deadline creates backlog rather than a source failure.
- For missed awake/offline time: let scheduled recovery apply the default 72-hour cap. Use `catch-up --start YYYY-MM-DD --end YYYY-MM-DD` only when the user selects an older interval.
- For schedule lifecycle: use `schedule install|pause|resume|status|uninstall`. Explain that locked-and-awake works; sleep, logout, shutdown, and offline time pause work.
- For an unarmed containment pilot: run `pilot extend-validation` and keep shadow mode active. For explicitly authorized automatic notification activation, complete one real human-approved revised draft, run `pilot notification-canary`, then use `pilot extend-validation --auto-activate`. Use `pilot readiness` for exact blockers; never deliver alerts older than the activation watermark.
- For ChatGPT assistance: release 0.3.7 requires a current release-bound isolation attestation. Check `assistance status`, run `assistance check-isolation`, and enable only after the signed bundled Codex, child-credential, filesystem, private-network, Unix-socket, broker, and no-tools checks pass. If any check fails, keep assistance disabled and continue deterministic monitoring.
- For drafting: require an explicit human approval already recorded in the Wire. Approval starts generation immediately; the durable queue and half-hour worker are recovery fallbacks. Never infer approval from viewing, discussing, or accepting a story.
- For draft attribution: name the confirmed original publication naturally and use the generated portable Markdown link. When only a republication URL is available, label it `Original Publisher, via Host`. In the normal path, Discovery links may remain in the grouped source list but never support a factual claim. A manual override may cite a validated stored Discovery URL without reclassifying it as qualifying evidence.
- For failed drafting: use the source-bound editable shell immediately when ChatGPT is unavailable. Saving the shell completes a manual draft and cancels its pending AI work. Otherwise inspect the exact visible work state and retry the preserved approval only after its evidence snapshot still passes. Never create a duplicate approval or work item.
- For deletion: create a purge preview first. Execute only the exact returned plan ID after explicit human confirmation; there is no backup or undo.

## Preserve boundaries

- Keep runtime data under the configured local data root and outside every Git worktree.
- Never add an OpenAI API key, paid fallback, publishing credential, browser cookie, proxy, or destination integration.
- Never publish automatically.
- Keep normal-path reporting neutral unless a Strong or Moderate open-source opportunity has a specific mechanism, evidence, counterargument, and explicit lens approval. A human-confirmed manual lens override may bypass that eligibility check; preserve the labelled lens structure and editorial tradeoffs.
- Treat discovery feeds and popularity as signals, not verification.
- Treat HuggingNews summaries, selected public posts, source breadth, ranks, and engagement as Discovery momentum only. Safely inspected linked pages still require human role and provenance confirmation before they affect verification.
- Keep normal qualification and drafting approval separate. Manual candidate approval is a distinct, warned override that may bypass drafting gates but must preserve their failed state and its audit snapshot.
- Never place manual-override badges, gate names, or system labels such as `unverified` or `provisional` into draft copy or exports.
- Preserve First-Public Time during catch-up; do not relabel older material as breaking.
- Treat source revisions separately from editorial material updates. Parser, hash-version, formatting, metadata, engagement, and duplicate changes never refresh the 24-hour ranking clock; only a changed normalized atomic-claim set does.
- Report source gaps, deferred work, isolation failures, and low-disk states plainly.

## Return useful results

Lead with operational state: last scan, next scan, queue, source failures, and candidate/watch counts. For editorial review, distinguish verified facts, attributed claims, uncertainty, and the optional separately labelled open-source lens. Include source links from the stored evidence bundle and avoid unsupported additions.
