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

### Route to existing topics

To separate the feed, enable Topics in a private Telegram supergroup and add
the paired bot with permission to send messages. Create these three topics:

- **Open-Source AI** — open-source AI models, tools and ecosystem developments;
  open-weight and source-available releases share this destination without
  implying that their licenses meet the open-source definition.
- **AI Policy & Law** — AI regulation, legislation, copyright, lawsuits,
  licensing, compliance and export controls.
- **AI Models & Products** — model/product launches, updates, capability and
  performance improvements, including proprietary models.

Copy a topic link (or a message link that includes its topic) for each destination:

```bash
open-source-ai-news-wire telegram topics \
  --topic open_source_ai=https://t.me/c/GROUP/OPEN_TOPIC \
  --topic ai_policy_law=https://t.me/c/GROUP/POLICY_TOPIC \
  --topic ai_models_products=https://t.me/c/GROUP/MODEL_TOPIC
open-source-ai-news-wire telegram send-test
open-source-ai-news-wire telegram enable
```

Replace the placeholders with actual numeric private-topic links from the same
group. The configuration command verifies that the target is a private forum
and the existing bot is a member; it does not create topics or send content.
It also supports moving the paired bot from its original group to the explicitly
specified forum group. Topic-administration rights are unnecessary for posting
to existing topics. Testing sends one message to each topic; all three must
succeed before enabling. Configuring topics stops the old delivery mode.

Routing uses independent keyword rules over the title and fetched summary,
never the publisher or the old single category. A new open-source model can
go to both Open-Source AI and AI Models & Products. An open-source AI legal
story can go to Open-Source AI and AI Policy & Law. Each matched topic gets
one copy of the same message. Stories matching none of the three remain on the
dashboard without a Telegram post or General-topic fallback. Model/product
improvements are now included in collection even without an AGI or legal angle.
These deterministic rules may miss stories whose titles and excerpts do not
state the relevant topic explicitly.

Each event's destinations are saved when it is collected. Per-topic delivery
receipts and retries prevent one failed topic from replaying successful sends
to another topic. The shared pacing limit applies across all copies. Existing
history is retained without reposting it. Reconfiguration requires the existing
topic queue to be drained; it cannot silently retarget queued messages.

### Message contents

Each message contains a bold headline, a clean source-provided excerpt when
available, and one linked publisher label. The same format applies to all three
topics. Fetch/publication timestamps, discovery tags, points, comments and
provenance boilerplate are omitted.

Each message uses this keyboard across all three topics:

| Row | Buttons |
|---|---|
| 1 | Copy link · Copy prompt · Search on Google |

Copy prompt uses one deterministic prompt builder with
the exact displayed headline, useful description when available, and source URL:

```text
Use the skill selected below to create a draft about this news item.

Title: {actual headline}
Description: {actual description}
Source: {actual source URL}

Skill: [CHOOSE AN INSTALLED SKILL]
```

The Description line is omitted when unavailable. Skill selection remains
editable; no host skill, model or workspace is silently chosen for the viewer.

Prompts up to 256 characters copy directly through Telegram. Longer prompts open
the public [NewsWire copy page](https://newswire-prompt-handoff.shadmanulhaque.chatgpt.site/)
without dashboard access. That page contains the complete editable text and one
**Copy full prompt** button. It has no app-opening actions. Historical handoff
URLs still display their complete prompts for copying.

The public handoff is a separate static Site. The prompt stays in the URL fragment
and is processed locally; no news database, credential, connector or AI endpoint
is accessed. It does not automatically copy, navigate or submit anything.
Copy success is reported only after `navigator.clipboard.writeText` resolves;
denial or missing clipboard support selects the complete text for manual copying.

Search on Google opens `https://www.google.com/search?q=...` with the unquoted,
whitespace-normalized headline, with display markup removed. It does not include
the description or prompt, generate search terms, scrape Google or use an API.
The standard HTTPS link opens on the clicking viewer's device according to their
Telegram/browser settings. The bot cannot force Chrome or override an in-app browser.

Excerpts use deterministic selection of up to three complete source sentences,
capped at 650 characters. Collection prefers usable feed excerpts, then safely
retrieves page metadata when needed. Site slogans, tag-only summaries and
incomplete trailing sentences are discarded. If retrieval fails or no useful
excerpt exists, the headline and source still appear. No automatic AI summary or
"Why it matters" text is generated.

Explicit underlying article/repository links are preferred to discovery links;
otherwise the actual discovery source remains accurately labelled. The source
link is shared consistently across the body and actions. Telegram clipboard
buttons accept at most 256 characters: a longer prompt opens the handoff with a
Copy full prompt action, never truncated. URLs remain complete; unusually long
source URLs retain the **Open source** fallback. Formatting applies to pending and
future deliveries; previously sent messages are not edited or replayed.

Every newly fetched relevant item is eligible, including discovery items and
items with unknown, invalid, future, or older publication dates. Publication
dates do not block sending or retries. Publication dates remain internal metadata and are not displayed. A social-post
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
  age does not expire a queued item; its original delivery identity remains unchanged.

Group members control Telegram notification sounds and banners themselves.
The Mac must be awake, logged in, and connected for collection and delivery.
No new AI call, public server, tunnel, or paid hosting is used by this feature.
