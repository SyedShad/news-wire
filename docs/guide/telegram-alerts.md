# Private Telegram alerts

Telegram alerts provide a small informational feed for one private team group.
They are sent by News Wire running on your Mac. They do not publish Reddit
content and do not expose the hosted dashboard.

## One-time setup

1. Open BotFather and use `/newbot` to create a bot through the normal flow.
2. Add the bot to the private team group and keep group privacy enabled.
3. From the Mac, run `telegram pair` and complete the short-lived pairing code
   and local destination confirmation. Enter the token hidden at the terminal;
   never paste it into Codex.
4. Run `telegram send-test` and verify the receipt in the group.
5. Run `telegram enable`.

```bash
open-source-ai-news-wire telegram pair
open-source-ai-news-wire telegram send-test
open-source-ai-news-wire telegram enable
open-source-ai-news-wire telegram status
```

The bot token is stored through native macOS Keychain APIs. It is never written
to configuration files, a LaunchAgent plist, logs, diagnostics, processing
packets, or Git. The configured destination is exactly one paired private group.

## What gets sent

Each message contains:

- the relevant article headline;
- publisher/source context;
- the time News Wire first fetched the item, plus its publication time when verified; and
- the public source link.

Messages do not include scores, priority, research instructions, drafts,
editorial controls, or hosted-dashboard actions. The existing relevance event
identity prevents tracking variants and rescans from producing a second alert.

Every newly fetched relevant item is eligible, including discovery items and
items with unknown, invalid, future, or older publication dates. Publication
dates do not block sending or retries. Messages label fetch time separately;
unreliable original publication dates are marked as not verified. A social-post
or aggregator date is never presented as the original article's publication date.
New relevant items collected during recovery or an explicit catch-up also qualify.
Enabling or re-enabling starts with new events and does not backfill older events.

## Scheduling, limits, and recovery

The Telegram LaunchAgent dispatches at load and every 60 seconds while the Mac
is awake. A cycle has a 50-second budget, sends at most 12 events, and waits
at least four seconds between send attempts, including across restarts. The existing scanner remains on its 30-minute
schedule. The Bot API uses ordinary free delivery only; paid broadcast,
payment, and Stars flows are disabled. See the [Telegram limits and cost FAQ](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this).

Sleep, logout, or offline state pauses work. The local ledger resumes after the
next awake launch, including items queued for more than 24 hours. A request whose response is lost after Telegram may have
accepted it is retried under the bounded at-least-once policy. A rare duplicate
is possible; the ledger records attempts and confirmed message IDs, but cannot prove
whether Telegram accepted a request whose response was lost.

Inspect or stop the lane with:

```bash
open-source-ai-news-wire telegram status
open-source-ai-news-wire telegram dispatch
open-source-ai-news-wire telegram disable
open-source-ai-news-wire telegram unpair
```

`disable` stops future delivery while retaining the ledger. `unpair` removes
the local destination and token association; pairing is required again before
delivery can resume.

## Troubleshooting

- `token_unavailable`: unlock the Mac login Keychain. If the token was removed,
  pair again, send a test, and enable. Never put the token in command arguments.
- `telegram_api_401`: the token is invalid or revoked; pair with the current token.
- `telegram_api_403`: restore the bot's group membership and sending permission,
  send a new test, then enable again.
- `cooldown`: Telegram requested a pause; the sender waits until the reported time.
- A failed request retries with increasing delays up to 30 minutes. Publication
  age does not expire a queued item; its fetched time remains visible in the message.

Group members control Telegram notification sounds and banners themselves.
The Mac must be awake, logged in, and connected for collection and delivery.
No new AI call, public server, tunnel, or paid hosting is used by this feature.
