# Telegram alerts

Telegram alerts are informational notifications for one private team group.
They are separate from Reddit drafting and publication, and separate from the
hosted dashboard.

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

Each alert contains a relevant headline, publisher/source context, fetched time,
publication time when verified, and public source link. It contains no draft, editorial
control, hosted-dashboard action, priority, score, or research instruction.

Every newly fetched relevant event qualifies regardless of its publication date.
Unknown, invalid, future, discovery, and older publication dates do not prevent
delivery or retries. Fetch time is labelled separately; unreliable original
publication dates are marked as not verified. Enabling or re-enabling starts at
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
