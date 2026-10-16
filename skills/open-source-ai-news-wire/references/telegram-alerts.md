# Telegram alerts

Telegram alerts are informational notifications for one private team group.
They are separate from Reddit drafting and publication, and separate from the
hosted dashboard.

## Existing forum topics

The sender can route to three existing topics in one private forum group:
**Open-Source AI**, **AI Policy & Law**, and **AI Models & Products**.
Use `telegram topics` with one `--topic KEY=LINK` per destination; keys are
`open_source_ai`, `ai_policy_law`, and `ai_models_products`. All three links
must identify distinct topics in the same private group. The existing paired
bot must have send permission there. No topic creation or admin rights are needed.

Configuration stops delivery until `telegram send-test` succeeds in all three
topics and `telegram enable` is run. Matching is independent and allows copies
in multiple topics. Each destination has its own durable receipt/retry state;
there is no General-topic fallback, historical replay, or change to Reddit
publication. See the [topic setup guide](../../../docs/guide/telegram-alerts.md#route-to-existing-topics)
for commands and classification details.

## Pair and enable

Open BotFather and use `/newbot` to create a bot through the normal flow. Add it to the private group with
group privacy enabled, then pair it locally:

```bash
open-source-ai-news-wire telegram pair
open-source-ai-news-wire telegram send-test
open-source-ai-news-wire telegram enable
open-source-ai-news-wire telegram status
```

The local pairing command uses a short-lived pairing code and requires confirmation of the
one private destination. The bot token is read through native macOS Keychain
APIs and is never placed in a config file, plist, processing packet, log,
diagnostic export, or Git worktree. `send-test` must verify a receipt before
delivery can be enabled.

## Message and eligibility contract

Each alert contains a bold headline, a clean source excerpt when available, one
linked publisher label, and five buttons: Copy link / Copy prompt, Open in Codex /
Open in Claude, and Search on Google. All three
topics use this format. Fetch/publication dates, discovery tags, engagement and
provenance boilerplate are omitted. Excerpts come from feed text or safely fetched
page metadata; formatting and buttons require no AI inference. Missing useful
text leaves a headline-and-source message. Clipboard payloads are bounded to
256 characters without truncating URLs; long links use one Open source button.
A shared full prompt populates title, optional description and source. Longer
prompts and native app buttons use a public HTTPS handoff requiring an explicit
copy/open action, without dashboard access or automatic submission. The skill
placeholder stays editable. Google uses only the normalized unquoted headline
and opens according to the clicking viewer's browser settings.
Pending and future deliveries use the format; previous messages are not replayed.

Every newly fetched relevant event qualifies regardless of its publication date.
Unknown, invalid, future, discovery, and older publication dates do not prevent
delivery or retries. Publication dates are retained internally and not displayed. Enabling or re-enabling starts at
the current event-ID cursor and never backfills older events.

The Bot API is used for ordinary free delivery only. Paid broadcast options,
payment and Stars flows, public channels, and unpaired groups are not allowed.

## Scheduling and recovery

The Telegram LaunchAgent runs at load and every 60 seconds while the Mac is
awake. Each dispatch has a 50-second budget, sends at most 12 items, and paces
them at four seconds per send. The existing scanner continues to run every 30
minutes. Sleep, logout, and offline periods stop work; a later awake launch
resumes from the durable ledger.

Disable, inspect, dispatch once, or remove the pairing with:

```bash
open-source-ai-news-wire telegram disable
open-source-ai-news-wire telegram status
open-source-ai-news-wire telegram dispatch
open-source-ai-news-wire telegram unpair
```

The ledger records attempts and confirmed Telegram message IDs. If the Mac
loses a response after Telegram may have accepted a request, the sender retries
even after more than 24 hours in the queue. A rare duplicate is possible; the ledger
cannot prove whether a request with a lost response was accepted.
