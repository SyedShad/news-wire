"""Source-only Telegram excerpts, attribution and clipboard actions; no inference."""

from __future__ import annotations

import html
import ipaddress
import json
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urlsplit


HANDOFF_BASE_URL = "https://newswire-prompt-handoff.shadmanulhaque.chatgpt.site/"


def literal(value: object) -> str:
    text = " ".join(html.unescape(str(value or "")).split())
    return " ".join("".join(c for c in text if unicodedata.category(c) not in {"Cc", "Cf"}).split())


def plain(value: object) -> str:
    return literal(re.sub(r"<[^>]+>", " ", str(value or "")))


def source_excerpt(value: object, *, publisher: str = "", title: str = "") -> str:
    text = plain(value)[:16000]
    if publisher:
        text = re.sub(r"^" + re.escape(publisher) + r" reports:\s*", "", text)
    text = re.split(r"This context comes from the monitored|Only limited context was available", text)[0]
    if re.match(r'^.+ published [“"]', text):
        return ""
    if re.search(r"discovery topics:|^Article URL:|^Abstract page for arXiv|"
                 r"journey to advance and democratize artificial intelligence", text, re.I):
        return ""
    text = re.split(r"(?:Comments URL:|📰 Source:|🔗 (?:Link|Archive):)", text)[0]
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"#\s*[\w-]+", "", text)
    text = re.sub(r"\bPoints:\s*\d+|\bComments:\s*\d+", "", text)
    text = " ".join(text.split()).strip()
    # Only complete sentences qualify. A truncated final sentence is discarded.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    sentences = [s.strip() for s in sentences if s.endswith((".", "!", "?"))
                 and not s.rstrip().endswith(("…", "...", "…."))]
    sentences = [s for s in sentences if s.casefold().strip('.!?') != title.casefold().strip('.!?')]
    contribution = next((i for i, s in enumerate(sentences)
                         if re.search(r"\bwe (?:propose|present|introduce|address)\b", s, re.I)), 0)
    selected: list[str] = []
    for sentence in sentences[contribution:contribution + 3]:
        if len(" ".join(selected + [sentence])) > 650:
            break
        selected.append(sentence)
    return " ".join(selected)


class _Descriptions(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.blocks: list[str] = []
        self._json = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in {"og:description", "description", "twitter:description"}:
                self.meta[key] = attrs.get("content") or ""
        if tag == "script" and (attrs.get("type") or "").lower() == "application/ld+json":
            self._json = True
            self.blocks.append("")

    def handle_data(self, data):
        if self._json:
            self.blocks[-1] += data

    def handle_endtag(self, tag):
        if tag == "script":
            self._json = False


def page_excerpt(payload: bytes, *, publisher: str = "", title: str = "") -> str:
    parser = _Descriptions()
    parser.feed(payload.decode("utf-8", errors="replace"))
    candidates = [parser.meta.get(key, "") for key in ("og:description", "description", "twitter:description")]

    def article_descriptions(value):
        if isinstance(value, dict):
            kinds = value.get("@type", [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if isinstance(kinds, list) and any(k in {"Article", "NewsArticle", "BlogPosting", "ScholarlyArticle"} for k in kinds if isinstance(k, str)):
                if isinstance(value.get("description"), str):
                    yield value["description"]
            for child in value.values():
                yield from article_descriptions(child)
        elif isinstance(value, list):
            for child in value:
                yield from article_descriptions(child)

    for block in parser.blocks[:12]:
        try:
            candidates.extend(article_descriptions(json.loads(block)))
        except (ValueError, RecursionError):
            continue
    return next((excerpt for candidate in candidates
                 if (excerpt := source_excerpt(candidate, publisher=publisher, title=title))), "")


def valid_source(value: str) -> bool:
    try:
        if any(c.isspace() or ord(c) < 32 for c in value):
            return False
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return False
        if parsed.port not in {None, 80 if parsed.scheme == "http" else 443}:
            return False
        if host == "localhost" or host.endswith((".localhost", ".local")):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True
    except ValueError:
        return False


def message_content(event: dict[str, object]) -> tuple[str, str, str, str]:
    title = literal(event.get("title"))[:500]
    publisher = literal(event.get("publisher"))[:120]
    source = str(event.get("canonical_url") or "").strip()
    context = str(event.get("context") or "")
    attribution = str(event.get("source_summary") or "") + " " + context
    explicit = re.search(r"(?:Article URL:|🔗 Link:)\s*(https?://[^\s<>]+)", attribution)
    if explicit and valid_source(html.unescape(explicit[1])):
        source = html.unescape(explicit[1])
    if valid_source(source) and str(event.get("source_type")) == "discovery":
        host = urlsplit(source).hostname or ""
        discovery_host = any(name in host for name in ("huggingnews.com", "news.ycombinator.com", "mastodon"))
        if not discovery_host:
            label = re.search(r"📰 Source:\s*(.+?)(?=🔗|$)", attribution)
            publisher = plain(label[1])[:120] if label else host
    excerpt = source_excerpt(event.get("source_summary"), publisher=publisher, title=title)
    excerpt = excerpt or source_excerpt(context, publisher=plain(event.get("publisher")), title=title)
    return title, publisher, source, excerpt


def build_prompt(title: str, description: str, source: str) -> str:
    """Build once from the exact visible message fields, without inference."""
    lines = ["Use the skill selected below to create a draft about this news item.",
             "", f"Title: {title}"]
    if description:
        lines.append(f"Description: {description}")
    lines.extend([f"Source: {source}", "", "Skill: [CHOOSE AN INSTALLED SKILL]"])
    return "\n".join(lines)


def handoff_url(prompt: str, action: str) -> str:
    return HANDOFF_BASE_URL + "#" + urlencode(
        {"v": "1", "action": action, "prompt": prompt}, quote_via=quote, safe="")


def copy_prompt_button(prompt: str) -> dict[str, object]:
    if len(prompt) <= 256:
        return {"text": "Copy prompt", "copy_text": {"text": prompt}}
    return {"text": "Copy prompt", "url": handoff_url(prompt, "copy")}


def buttons(source: str, title: str, description: str = "") -> dict[str, object]:
    prompt = build_prompt(title, description, source)
    link_button = ({"text": "Copy link", "copy_text": {"text": source}} if len(source) <= 256
                   else {"text": "Open source", "url": source})
    return {"inline_keyboard": [[
        link_button, copy_prompt_button(prompt),
        {"text": "Search on Google", "url": "https://www.google.com/search?" +
         urlencode({"q": plain(title)}, quote_via=quote, safe="")},
    ]]}
