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

`validation_status` records whether a definition passed the live check, is `validated-unavailable`, or is `excluded-by-operator`. `evidence_use` records primary, reporting, or discovery-only use. Neither field overrides the runtime evidence gates.

On 2026-07-17, the five remaining non-SEC families passed bounded no-login access and parser checks:

- Anthropic Newsroom: dedicated dated Event parser.
- Meta AI Blog: dedicated dated Event parser replacing the unavailable RSS address.
- CISA advisories: dedicated public HTML Event parser with deterministic AI relevance terms; the declared product client still receives HTTP 403, so it remains `validated-unavailable` and disabled.
- HuggingNews: dated Discovery parser; rank and momentum remain explicitly non-evidentiary.
- Mastodon AI tag: bounded Discovery RSS parser requiring AI context plus an event, research, or policy term and rejecting common promotional noise.

Together with the previously validated registry, nineteen definitions are live-validated, CISA is implemented but `validated-unavailable`, and SEC remains disabled as `excluded-by-operator`. Release 0.3.1 makes no SEC requests.

Run `sources health` after installation and after any source-definition update. A failing source is isolated; its cursor advances only after a successful transaction. Whole-Mac resolver or connection outages are grouped and do not advance individual source failure streaks. Re-enable a disabled source only after updating its checked date, fixture, live result, evidence role, and detail.
