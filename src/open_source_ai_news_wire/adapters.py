"""Bounded adapters that normalize public source responses."""

from __future__ import annotations

import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


class AdapterError(ValueError):
    """Raised when a source response cannot be parsed safely."""


@dataclass(frozen=True, slots=True)
class Observation:
    external_id: str
    title: str
    url: str
    published_at: str
    summary: str = ""
    language: str = "und"
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        normalized = re.sub(r"[^a-z0-9]+", " ", self.title.lower()).strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @property
    def content_hash(self) -> str:
        # Mutable counters and other attention metadata must not promote a
        # routine rescan into a material editorial update.  Adapters may
        # provide a stable claim-bearing summary while retaining their full
        # display summary and metrics separately.
        material_summary = str(self.metadata.get("material_summary", self.summary))
        return hashlib.sha256(f"{self.title}\n{material_summary}".encode("utf-8")).hexdigest()


def canonical_url(value: str, base_url: str) -> str:
    absolute = urljoin(base_url, value.strip())
    parsed = urlsplit(absolute)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise AdapterError("Item URL is not a valid web URL")
    filtered = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"ref", "source", "fbclid", "gclid"}
    ]
    host = parsed.hostname.lower().rstrip(".")
    port = f":{parsed.port}" if parsed.port else ""
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urlunsplit((parsed.scheme.lower(), f"{host}{port}", path, urlencode(filtered), ""))


def parse_public_time(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    raw = value.strip()
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            moment = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError) as error:
            moment = None
            for pattern in ("%b %d, %Y", "%B %d, %Y", "%A, %b %d, %Y", "%A, %B %d, %Y"):
                try:
                    moment = datetime.strptime(raw, pattern).replace(tzinfo=UTC)
                    break
                except ValueError:
                    continue
            if moment is None:
                raise AdapterError(f"Invalid publication time: {raw}") from error
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _text(element: ET.Element, names: tuple[str, ...]) -> str:
    for child in element.iter():
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local in names and child.text:
            return " ".join(child.text.split())
    return ""


def parse_feed(payload: bytes, *, source_url: str, observed_at: str, limit: int = 200) -> list[Observation]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise AdapterError("Malformed XML feed") from error
    root_name = root.tag.rsplit("}", 1)[-1].lower()
    if root_name == "feed":
        entries = [element for element in root if element.tag.rsplit("}", 1)[-1].lower() == "entry"]
    else:
        entries = [element for element in root.iter() if element.tag.rsplit("}", 1)[-1].lower() == "item"]
    observations: list[Observation] = []
    for entry in entries[:limit]:
        title = _text(entry, ("title",))
        published = _text(entry, ("published", "pubdate", "updated", "date"))
        summary = _text(entry, ("summary", "description", "content"))
        external_id = _text(entry, ("id", "guid"))
        link = ""
        for child in entry.iter():
            if child.tag.rsplit("}", 1)[-1].lower() != "link":
                continue
            candidate = child.attrib.get("href") or (child.text or "")
            relationship = child.attrib.get("rel", "alternate")
            if candidate and relationship in {"alternate", ""}:
                link = candidate.strip()
                break
        if not link:
            link = _text(entry, ("link",))
        if not title or not link:
            continue
        item_url = canonical_url(link, source_url)
        observations.append(
            Observation(
                external_id=external_id or item_url,
                title=title[:500],
                url=item_url,
                published_at=parse_public_time(published, observed_at),
                summary=re.sub(r"<[^>]+>", " ", summary)[:4000].strip(),
                language=entry.attrib.get("{http://www.w3.org/XML/1998/namespace}lang", "und"),
            )
        )
    return observations


def parse_json(payload: bytes, *, source_url: str, observed_at: str, limit: int = 200) -> list[Observation]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AdapterError("Malformed JSON source") from error
    if isinstance(value, dict):
        rows = value.get("items") or value.get("results") or value.get("entries") or []
    else:
        rows = value
    if not isinstance(rows, list):
        raise AdapterError("JSON source does not contain an item list")
    observations: list[Observation] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("name") or "").strip()
        link = str(row.get("url") or row.get("html_url") or row.get("link") or "").strip()
        if not title or not link:
            continue
        item_url = canonical_url(link, source_url)
        observations.append(
            Observation(
                external_id=str(row.get("id") or row.get("document_number") or row.get("guid") or item_url),
                title=title[:500],
                url=item_url,
                published_at=parse_public_time(
                    str(
                        row.get("published_at")
                        or row.get("publication_date")
                        or row.get("published")
                        or row.get("date")
                        or ""
                    ),
                    observed_at,
                ),
                summary=str(row.get("summary") or row.get("abstract") or row.get("description") or "")[:4000],
                language=str(row.get("language") or "und")[:16],
            )
        )
    return observations


def parse_sitemap(payload: bytes, *, source_url: str, observed_at: str, limit: int = 200) -> list[Observation]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise AdapterError("Malformed sitemap XML") from error
    observations: list[Observation] = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1].lower() != "url":
            continue
        link = _text(node, ("loc",))
        if not link:
            continue
        item_url = canonical_url(link, source_url)
        slug = urlsplit(item_url).path.rstrip("/").rsplit("/", 1)[-1]
        title = re.sub(r"[-_]", " ", slug).strip() or item_url
        observations.append(
            Observation(
                external_id=item_url,
                title=title[:500],
                url=item_url,
                published_at=parse_public_time(_text(node, ("lastmod",)), observed_at),
            )
        )
        if len(observations) >= limit:
            break
    return observations


class _ListingParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.current_href: str | None = None
        self.current_text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            attributes = dict(attrs)
            self.current_href = attributes.get("href")
            self.current_text = []

    def handle_data(self, data: str) -> None:
        if self.current_href is not None:
            self.current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self.current_href is not None:
            text = " ".join(" ".join(self.current_text).split())
            if text:
                self.links.append((self.current_href, text))
            self.current_href = None
            self.current_text = []


def parse_html_listing(payload: bytes, *, source_url: str, observed_at: str, limit: int = 200) -> list[Observation]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AdapterError("HTML listing is not UTF-8") from error
    parser = _ListingParser()
    parser.feed(text)
    observations: list[Observation] = []
    seen: set[str] = set()
    for link, title in parser.links:
        try:
            item_url = canonical_url(link, source_url)
        except AdapterError:
            continue
        if item_url in seen or len(title) < 12:
            continue
        seen.add(item_url)
        observations.append(
            Observation(item_url, title[:500], item_url, observed_at)
        )
        if len(observations) >= limit:
            break
    return observations


_ANCHOR_PATTERN = re.compile(
    r"<a\b(?P<attrs>[^>]*)href=[\"'](?P<href>[^\"']+)[\"'](?P<tail>[^>]*)>(?P<body>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_TIME_PATTERN = re.compile(
    r"<time\b(?P<attrs>[^>]*)>(?P<label>.*?)</time>",
    re.IGNORECASE | re.DOTALL,
)
_HUMAN_DATE_PATTERN = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\b",
    re.IGNORECASE,
)


def _plain_html(value: str) -> str:
    without_comments = re.sub(r"<!--.*?-->", " ", value, flags=re.DOTALL)
    without_tags = re.sub(r"<[^>]+>", " ", without_comments)
    return " ".join(html.unescape(without_tags).split())


def _nearest_public_time(text: str, position: int, observed_at: str) -> str:
    candidates: list[tuple[int, str]] = []
    for match in _TIME_PATTERN.finditer(text, max(0, position - 1200), min(len(text), position + 1200)):
        datetime_attribute = re.search(r"datetime=[\"']([^\"']+)[\"']", match.group("attrs"), re.IGNORECASE)
        value = datetime_attribute.group(1) if datetime_attribute else _plain_html(match.group("label"))
        if value:
            distance = abs(match.start() - position)
            candidates.append((max(0, distance - 100) if datetime_attribute else distance, value))
    for match in _HUMAN_DATE_PATTERN.finditer(text, max(0, position - 1000), min(len(text), position + 1000)):
        candidates.append((abs(match.start() - position), match.group(0)))
    for _distance, value in sorted(candidates):
        try:
            return parse_public_time(value, observed_at)
        except AdapterError:
            continue
    return observed_at


def _parse_dated_links(
    payload: bytes,
    *,
    source_url: str,
    observed_at: str,
    accepted_path: re.Pattern[str],
    limit: int = 200,
) -> list[Observation]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AdapterError("HTML listing is not UTF-8") from error
    selected: dict[str, Observation] = {}
    for match in _ANCHOR_PATTERN.finditer(text):
        try:
            item_url = canonical_url(html.unescape(match.group("href")), source_url)
        except AdapterError:
            continue
        if not accepted_path.search(urlsplit(item_url).path):
            continue
        body = match.group("body")
        title = _plain_html(body)
        heading = re.search(r"<h[1-4]\b[^>]*>(.*?)</h[1-4]>", body, re.IGNORECASE | re.DOTALL)
        if heading:
            title = _plain_html(heading.group(1))
        else:
            titled = re.search(
                r"<(?:span|div)\b[^>]*class=[\"'][^\"']*(?:title|headline)[^\"']*[\"'][^>]*>(.*?)</(?:span|div)>",
                body,
                re.IGNORECASE | re.DOTALL,
            )
            if titled:
                title = _plain_html(titled.group(1))
        aria = re.search(r"aria-label=[\"'](?:Read\s+)?([^\"']+)[\"']", match.group("attrs") + match.group("tail"), re.IGNORECASE)
        if aria and len(_plain_html(aria.group(1))) > len(title):
            title = _plain_html(aria.group(1))
        if len(title) < 12 or title.lower() in {"featured", "read more", "learn more"}:
            continue
        item = Observation(
            external_id=item_url,
            title=title[:500],
            url=item_url,
            published_at=_nearest_public_time(text, match.start(), observed_at),
        )
        previous = selected.get(item_url)
        if previous is None or len(item.title) >= len(previous.title):
            selected[item_url] = item
        if len(selected) >= limit:
            break
    return list(selected.values())


def parse_anthropic_newsroom(payload: bytes, *, source_url: str, observed_at: str) -> list[Observation]:
    return _parse_dated_links(
        payload,
        source_url=source_url,
        observed_at=observed_at,
        accepted_path=re.compile(r"^/news/[^/?#]+/?$"),
    )


def parse_meta_ai_blog(payload: bytes, *, source_url: str, observed_at: str) -> list[Observation]:
    return _parse_dated_links(
        payload,
        source_url=source_url,
        observed_at=observed_at,
        accepted_path=re.compile(r"^/blog/[^/?#]+/?$"),
    )


def parse_cisa_advisories(payload: bytes, *, source_url: str, observed_at: str) -> list[Observation]:
    return _parse_dated_links(
        payload,
        source_url=source_url,
        observed_at=observed_at,
        accepted_path=re.compile(r"^/news-events/(?:cybersecurity-advisories|alerts)/.+"),
    )


def parse_huggingnews(payload: bytes, *, source_url: str, observed_at: str, limit: int = 200) -> list[Observation]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AdapterError("HuggingNews listing is not UTF-8") from error
    token_pattern = re.compile(
        r"<h2\b[^>]*class=[\"'][^\"']*day-date[^\"']*[\"'][^>]*>(?P<date>.*?)</h2>|"
        r"<a\b(?P<attrs>[^>]*)class=[\"'][^\"']*story-row-link[^\"']*[\"'][^>]*href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<body>.*?)</a>",
        re.IGNORECASE | re.DOTALL,
    )
    current_date = observed_at
    observations: list[Observation] = []
    seen: set[str] = set()
    for match in token_pattern.finditer(text):
        if match.group("date") is not None:
            current_date = parse_public_time(_plain_html(match.group("date")), observed_at)
            continue
        try:
            item_url = canonical_url(html.unescape(match.group("href")), source_url)
        except AdapterError:
            continue
        if item_url in seen or not re.match(r"^/(?:ai|cybersecurity|tech|startups|earnings)/", urlsplit(item_url).path):
            continue
        body = match.group("body")
        title_match = re.search(r"class=[\"'][^\"']*story-title[^\"']*[\"'][^>]*>(.*?)</div>", body, re.IGNORECASE | re.DOTALL)
        title = _plain_html(title_match.group(1) if title_match else body)
        if len(title) < 12:
            continue
        rank_match = re.search(r"class=[\"'][^\"']*story-rank[^\"']*[\"'][^>]*>(.*?)</div>", body, re.IGNORECASE | re.DOTALL)
        signal_match = re.search(r"class=[\"'][^\"']*meta-signal[^\"']*[\"'][^>]*>(.*?)</span>", body, re.IGNORECASE | re.DOTALL)
        metadata = []
        if rank_match:
            metadata.append(f"rank {_plain_html(rank_match.group(1))}")
        if signal_match:
            metadata.append(f"momentum {_plain_html(signal_match.group(1))}")
        summary = "Discovery signal metadata"
        if metadata:
            summary += ": " + "; ".join(metadata)
        summary += ". Popularity is not evidence."
        observations.append(Observation(item_url, title[:500], item_url, current_date, summary))
        seen.add(item_url)
        if len(observations) >= limit:
            break
    return observations


def _huggingnews_millis(value: object, fallback: str) -> str:
    if value is None:
        return fallback
    try:
        moment = datetime.fromtimestamp(float(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OverflowError) as error:
        raise AdapterError("Invalid HuggingNews timestamp") from error
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_huggingnews_json(
    payload: bytes,
    *,
    source_url: str,
    observed_at: str,
    limit: int = 200,
) -> list[Observation]:
    """Parse the documented anonymous latest-feed contract."""
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AdapterError("Malformed HuggingNews JSON") from error
    if not isinstance(value, dict):
        raise AdapterError("HuggingNews JSON is not an object")
    if isinstance(value.get("dayGroups"), list):
        groups = value["dayGroups"]
    elif isinstance(value.get("stories"), list):
        # The documented anonymous search contract uses a top-level list.
        groups = [{"stories": value["stories"]}]
    else:
        raise AdapterError("HuggingNews JSON does not contain dayGroups or stories")
    observations: list[Observation] = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("stories"), list):
            continue
        for row in group["stories"]:
            if not isinstance(row, dict):
                continue
            slug = str(row.get("slug") or "").strip()
            title = str(row.get("title") or "").strip()
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,199}", slug) or len(title) < 12:
                continue
            try:
                published_at = _huggingnews_millis(row.get("publishedAt"), observed_at)
            except AdapterError:
                continue
            event_value = str(row.get("eventTimeApprox") or "").strip()
            if event_value:
                try:
                    event_at = parse_public_time(event_value, published_at)
                except AdapterError:
                    event_at = published_at
            else:
                event_at = published_at
            topic_tags = [
                {"slug": str(tag.get("slug") or "")[:100], "name": str(tag.get("name") or "")[:100]}
                for tag in row.get("topicTags", [])
                if isinstance(tag, dict) and tag.get("slug")
            ]
            topic_names = ", ".join(tag["name"] for tag in topic_tags if tag["name"])
            observations.append(
                Observation(
                    external_id=slug,
                    title=title[:500],
                    url=f"https://huggingnews.com/ai/{slug}",
                    published_at=event_at,
                    summary=f"HuggingNews discovery topics: {topic_names}."[:4000],
                    metadata={
                        "slug": slug,
                        "aggregator_published_at": published_at,
                        "event_time_approx": event_at,
                        "topic_tags": topic_tags,
                    },
                )
            )
            if len(observations) >= limit:
                return observations
    return observations


def enrich_huggingnews_detail(observation: Observation, payload: bytes) -> Observation:
    """Attach bounded reference metadata and public Discovery leads."""
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AdapterError("Malformed HuggingNews detail JSON") from error
    if not isinstance(value, dict) or str(value.get("slug") or "") != observation.external_id:
        raise AdapterError("HuggingNews detail does not match the requested story")
    selected: list[dict[str, object]] = []
    short_links: set[str] = set()
    for item in value.get("selectedTweets", []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        try:
            normalized = canonical_url(url, "https://x.com/")
        except AdapterError:
            continue
        if urlsplit(normalized).hostname not in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
            continue
        text = str(item.get("text") or "")[:4000]
        quoted_text = str(item.get("quotedTweetText") or "")[:4000]
        for candidate in re.findall(r"https://t\.co/[A-Za-z0-9]+", f"{text} {quoted_text}"):
            short_links.add(candidate)
        selected.append(
            {
                "external_id": normalized,
                "author_handle": str(item.get("authorHandle") or "").lstrip("@").lower()[:100],
                "url": normalized,
                "published_at": _huggingnews_millis(item.get("tweetedAt"), observation.published_at),
                "text": text,
                "quoted_text": quoted_text,
            }
        )
    metadata = dict(observation.metadata)
    metadata.update(
        {
            "reference_summary": str(value.get("summary") or "")[:8000],
            "selected_tweets": selected[:50],
            "short_links": sorted(short_links)[:50],
            "distinct_identity_count": len({item["author_handle"] for item in selected if item["author_handle"]}),
            "detail_fetched": True,
        }
    )
    return Observation(
        observation.external_id,
        observation.title,
        observation.url,
        observation.published_at,
        observation.summary,
        observation.language,
        metadata,
    )


def parse_huggingnews_momentum(payload: bytes) -> dict[str, dict[str, int]]:
    """Parse only the public, visible rank and post/account counters."""
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AdapterError("HuggingNews listing is not UTF-8") from error
    result: dict[str, dict[str, int]] = {}
    pattern = re.compile(
        r"story-row-link[^>]*href=[\"']/(?:ai|cybersecurity|tech|startups|earnings)/(?P<slug>[a-z0-9-]+)[\"'].*?"
        r"story-rank[^>]*>(?P<rank>\d+)</div>.*?meta-signal[^>]*>(?P<posts>\d+)\s*/\s*(?P<accounts>\d+)</span>",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(text):
        result[match.group("slug")] = {
            "daily_rank": int(match.group("rank")),
            "post_count": int(match.group("posts")),
            "account_count": int(match.group("accounts")),
        }
    return result


def parse_mastodon_signal(payload: bytes, *, source_url: str, observed_at: str, limit: int = 200) -> list[Observation]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise AdapterError("Malformed Mastodon RSS feed") from error
    signal_terms = re.compile(
        r"\b(model|release|research|paper|policy|regulation|benchmark|safety|security|incident|"
        r"open[ -](?:source|weight)|funding|acqui(?:res|red|sition)|agent(?:ic|s)?|llm|agi|"
        r"artificial general intelligence)\b",
        re.IGNORECASE,
    )
    ai_context = re.compile(
        r"\b(ai|artificial intelligence|machine learning|model(?:s|ing)?|llm|agi|"
        r"artificial general intelligence|agentic|open[ -](?:source|weight))\b",
        re.IGNORECASE,
    )
    noise_terms = re.compile(r"\b(commissions?|for sale|subscribe|prompt pack|daily horoscope|nft)\b", re.IGNORECASE)
    observations: list[Observation] = []
    for entry in [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() == "item"]:
        description = _text(entry, ("description", "content"))
        plain = _plain_html(description)
        context = re.sub(r"#\s*[\w-]+", " ", plain)
        if (
            len(context) < 40
            or not ai_context.search(context)
            or not signal_terms.search(context)
            or noise_terms.search(context)
        ):
            continue
        link = _text(entry, ("link",))
        if not link:
            continue
        item_url = canonical_url(link, source_url)
        title = re.split(r"(?:\n|(?<=[.!?])\s+)", plain, maxsplit=1)[0].strip()
        observations.append(
            Observation(
                external_id=_text(entry, ("guid", "id")) or item_url,
                title=title[:500],
                url=item_url,
                published_at=parse_public_time(_text(entry, ("pubdate", "published", "updated")), observed_at),
                summary=plain[:4000],
            )
        )
        if len(observations) >= limit:
            break
    return observations


def parse_source(
    adapter: str,
    payload: bytes,
    *,
    source_url: str,
    observed_at: str,
) -> list[Observation]:
    if adapter in {"feed", "github_release", "research_feed"}:
        return parse_feed(payload, source_url=source_url, observed_at=observed_at)
    if adapter == "json":
        return parse_json(payload, source_url=source_url, observed_at=observed_at)
    if adapter == "sitemap":
        return parse_sitemap(payload, source_url=source_url, observed_at=observed_at)
    if adapter == "html_listing":
        return parse_html_listing(payload, source_url=source_url, observed_at=observed_at)
    if adapter == "anthropic_newsroom":
        return parse_anthropic_newsroom(payload, source_url=source_url, observed_at=observed_at)
    if adapter == "meta_ai_blog":
        return parse_meta_ai_blog(payload, source_url=source_url, observed_at=observed_at)
    if adapter == "cisa_advisories":
        return parse_cisa_advisories(payload, source_url=source_url, observed_at=observed_at)
    if adapter == "huggingnews":
        return parse_huggingnews(payload, source_url=source_url, observed_at=observed_at)
    if adapter == "huggingnews_json":
        return parse_huggingnews_json(payload, source_url=source_url, observed_at=observed_at)
    if adapter == "mastodon_signal":
        return parse_mastodon_signal(payload, source_url=source_url, observed_at=observed_at)
    raise AdapterError(f"Unsupported adapter: {adapter}")
