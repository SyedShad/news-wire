"""Bounded adapters that normalize public source responses."""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
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

    @property
    def fingerprint(self) -> str:
        normalized = re.sub(r"[^a-z0-9]+", " ", self.title.lower()).strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.title}\n{self.summary}".encode("utf-8")).hexdigest()


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
    raise AdapterError(f"Unsupported adapter: {adapter}")
