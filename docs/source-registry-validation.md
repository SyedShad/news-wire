# Source registry validation

The tracked registry is dated and destination-neutral. It includes enabled sources and disabled coverage placeholders so missing access is explicit rather than silently ignored.

An enabled source must satisfy all of the following:

- public and usable without an account, cookie, payment, or paywall circumvention;
- an intended syndication/API surface or a deliberately reviewed public listing;
- HTTPS, registered host, public DNS, bounded response, and supported content type;
- a deterministic adapter fixture plus a current live parser check;
- a monitoring role of Event, Reporting, or Discovery that matches its evidentiary value;
- a conservative interval and, for general feeds, source-level include/exclude terms;
- no known access or policy conflict found during the dated review.

`validation_status` records whether a definition passed the live check or remains disabled for access, parser, or policy review. `evidence_use` records primary, reporting, or discovery-only use. Neither field overrides the runtime evidence gates.

On 2026-07-14, fifteen sources passed a controlled live run with no login and no paid service. The registry also keeps disabled entries for Anthropic's undated HTML listing, Meta's unavailable former RSS endpoint, SEC filings requiring a compliant enriched adapter, CISA access rejected by the pilot client, HuggingNews pending a dedicated parser, and Mastodon pending shadow-run noise/rate review.

Run `sources health` after installation and after any source-definition update. A failing source is isolated; its cursor advances only after a successful transaction. Re-enable a disabled source only after updating its checked date, fixture, live result, evidence role, and detail.
