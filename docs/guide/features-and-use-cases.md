# Features and use cases

## Coverage

| Lane | What belongs there |
|---|---|
| Open Ecosystem News | Open-source, open-weight, source-available, open-governance, model-hub, and open development news |
| AGI Development | Research, evaluations, capabilities, safety work, and public claims directly related to AGI |
| Broader AI News | AI developments directly concerning privacy, security, or regulation |

Topic relevance determines feed eligibility. The inbox shows each distinct relevant fetched article, including older publications and articles with unknown publication dates. Quality scores, publisher trust, and legacy story grades do not hide articles or block generation.

## Content and graphics

Open an article to choose **Generate content** or **Generate graphics**. Each task has independent runtime model and ordered skill selectors, with optional customizations. Choose any available skills suited to the desired output. The selected skills determine research and production workflows.

Graphics do not require a draft. Content results are editable, copyable, and downloadable. Graphic results have image previews and artifact downloads. Each job records its model, selected skills, customizations, and source snapshot. Regeneration and manual revisions preserve older outputs.

## Questions and recovery

Interactive questions appear in the task that requested them. Jobs support retry and cancellation. A stopped worker leaves a visible recoverable failure instead of silently repeating generation. Offline commands and outputs remain durable. A generation failure never changes article eligibility.

## Operations

Operational Review contains system notices with its own unread count. Source updates do not invalidate articles or generated work. Existing notification ledgers, archive choices, historical drafts, and edits survive migration; backfilling articles does not send historical alerts or start generation.

Source health, enablement, schedule, and recovery controls remain available. The scheduler runs at `:00` and `:30` while the Mac is awake. Automatic recovery covers up to 72 hours; explicit catch-up is available for older intervals.

## Local execution and hosted access

The local dashboard is loopback-only and one-time-link protected. The hosted dashboard receives a redacted projection and relays authenticated commands to the Mac. Tokens and internal file paths stay local; artifact transfers are bounded.

Generation uses Sign in with ChatGPT for a local/open-source integration. No paid API fallback or publishing integration is added. The current provider lacks native image generation and hosted connectors; the installed OpenAGI graphics skill uses its local renderer. Validate real local and hosted generation before enabling hosted execution.
