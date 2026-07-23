from __future__ import annotations

import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from open_source_ai_news_wire.adapters import (
    AdapterError,
    Observation,
    canonical_url,
    parse_feed,
    parse_html_listing,
    parse_anthropic_newsroom,
    parse_cisa_advisories,
    parse_huggingnews,
    parse_huggingnews_json,
    enrich_huggingnews_detail,
    parse_huggingnews_momentum,
    parse_json,
    parse_mastodon_signal,
    parse_meta_ai_blog,
    parse_public_time,
    parse_source,
    parse_sitemap,
)
from open_source_ai_news_wire.collector import Collector, _default_client_factory, _similarity, _title_tokens
from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.demo import seed_demo_data
from open_source_ai_news_wire.network import (
    ResponseTooLarge,
    SafeHttpClient,
    UnsafeRequest,
    resolve_known_short_url,
)
from open_source_ai_news_wire.qualification import qualify
from open_source_ai_news_wire.source_registry import set_source_enabled, synchronize_sources
from open_source_ai_news_wire.storage import Database


PUBLIC_IP = lambda _host: ["93.184.216.34"]


def test_safe_client_rejects_unregistered_private_and_credentialed_urls() -> None:
    with SafeHttpClient(allowed_hosts={"example.com"}, resolver=PUBLIC_IP) as client:
        with pytest.raises(UnsafeRequest, match="registered"):
            client.fetch("https://other.example/news")
        with pytest.raises(UnsafeRequest, match="credentials"):
            client.fetch("https://user:pass@example.com/news")
    with SafeHttpClient(allowed_hosts={"example.com"}, resolver=lambda _host: ["127.0.0.1"]) as client:
        with pytest.raises(UnsafeRequest, match="public"):
            client.fetch("https://example.com/news")


def test_safe_client_bounds_redirects_size_and_conditionals() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "https://evil.example/news"})
        if request.url.path == "/large":
            return httpx.Response(200, content=b"12345", headers={"content-type": "application/xml"})
        assert request.headers["if-none-match"] == '"v1"'
        return httpx.Response(304, headers={"etag": '"v1"'})

    transport = httpx.MockTransport(handler)
    with SafeHttpClient(
        allowed_hosts={"example.com"}, resolver=PUBLIC_IP, transport=transport, maximum_bytes=4
    ) as client:
        with pytest.raises(UnsafeRequest, match="registered"):
            client.fetch("https://example.com/redirect")
        with pytest.raises(ResponseTooLarge):
            client.fetch("https://example.com/large")
        result = client.fetch("https://example.com/feed", etag='"v1"')
        assert result.not_modified is True


def test_safe_client_rejects_protocol_redirect_and_content_type_edge_cases() -> None:
    with SafeHttpClient(allowed_hosts={"example.com"}, resolver=PUBLIC_IP) as client:
        with pytest.raises(UnsafeRequest, match="Only web"):
            client.fetch("file:///tmp/news")
        with pytest.raises(UnsafeRequest, match="Plain HTTP"):
            client.fetch("http://example.com/feed")
        with pytest.raises(UnsafeRequest, match="public"):
            SafeHttpClient(allowed_hosts={"example.com"}, resolver=lambda _host: []).fetch(
                "https://example.com/feed"
            )

    responses = {
        "/missing": httpx.Response(302),
        "/loop": httpx.Response(302, headers={"location": "/loop"}),
        "/binary": httpx.Response(200, content=b"x", headers={"content-type": "image/png"}),
        "/ok": httpx.Response(200, content=b"ok", headers={"content-type": "text/plain"}),
    }
    transport = httpx.MockTransport(lambda request: responses[request.url.path])
    with SafeHttpClient(
        allowed_hosts={"example.com"}, resolver=PUBLIC_IP, transport=transport, maximum_redirects=1
    ) as client:
        with pytest.raises(UnsafeRequest, match="omitted"):
            client.fetch("https://example.com/missing")
        with pytest.raises(UnsafeRequest, match="Too many"):
            client.fetch("https://example.com/loop")
        with pytest.raises(UnsafeRequest, match="content type"):
            client.fetch("https://example.com/binary")
        result = client.fetch("https://example.com/ok", last_modified="yesterday")
        assert result.body == b"ok"


def test_known_short_link_resolution_validates_every_public_https_hop() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "t.co":
            return httpx.Response(
                302,
                headers={"location": "https://publisher.example/article"},
                request=request,
            )
        return httpx.Response(200, content=b"article", request=request)

    resolved = resolve_known_short_url(
        "https://t.co/abc123",
        resolver=PUBLIC_IP,
        transport=httpx.MockTransport(handler),
    )
    assert resolved == "https://publisher.example/article"

    with pytest.raises(UnsafeRequest, match="known public short-link"):
        resolve_known_short_url(
            "https://example.com/not-short",
            resolver=PUBLIC_IP,
            transport=httpx.MockTransport(handler),
        )


def test_known_short_link_rejects_private_redirect_and_dns_change() -> None:
    private_redirect = httpx.MockTransport(
        lambda request: httpx.Response(
            302,
            headers={"location": "https://private.example/article"},
            request=request,
        )
    )
    with pytest.raises(UnsafeRequest, match="exclusively to public"):
        resolve_known_short_url(
            "https://t.co/abc123",
            resolver=lambda host: ["127.0.0.1"] if host == "private.example" else PUBLIC_IP(host),
            transport=private_redirect,
        )

    calls = 0

    def changing_resolver(_host: str) -> list[str]:
        nonlocal calls
        calls += 1
        return ["93.184.216.34" if calls == 1 else "93.184.216.35"]

    same_host_redirect = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"location": "/again"}, request=request)
    )
    with pytest.raises(UnsafeRequest, match="DNS resolution changed"):
        resolve_known_short_url(
            "https://t.co/abc123",
            resolver=changing_resolver,
            transport=same_host_redirect,
        )


def test_feed_json_sitemap_and_canonical_normalization() -> None:
    observed = "2026-07-14T10:00:00Z"
    feed = b"""<?xml version='1.0'?>
    <rss><channel><item><guid>one</guid><title>Open-source AI release</title>
    <link>https://example.com/news?utm_source=x&amp;id=1#top</link>
    <pubDate>Tue, 14 Jul 2026 09:00:00 GMT</pubDate><description>Details</description>
    </item></channel></rss>"""
    items = parse_feed(feed, source_url="https://example.com/feed", observed_at=observed)
    assert items[0].url == "https://example.com/news?id=1"
    assert items[0].published_at == "2026-07-14T09:00:00Z"
    assert parse_json(
        json.dumps({"items": [{"document_number": "2", "title": "AI policy", "html_url": "/policy", "publication_date": "2026-07-14", "abstract": "Rule"}]}).encode(),
        source_url="https://example.com/api", observed_at=observed,
    )[0].url == "https://example.com/policy"
    assert parse_sitemap(
        b"<urlset><url><loc>https://example.com/ai-update</loc><lastmod>2026-07-14</lastmod></url></urlset>",
        source_url="https://example.com/sitemap.xml", observed_at=observed,
    )[0].title == "ai update"
    assert canonical_url("/a?fbclid=x&q=1", "https://example.com") == "https://example.com/a?q=1"
    with pytest.raises(AdapterError):
        canonical_url("file:///tmp/private", "https://example.com")


def test_adapter_variants_and_malformed_payloads() -> None:
    observed = "2026-07-14T10:00:00Z"
    atom = b"""<feed xmlns='http://www.w3.org/2005/Atom'><entry xml:lang='en'>
    <id>a</id><title>AI model launch</title><updated>2026-07-14T09:00:00Z</updated>
    <link rel='alternate' href='/launch'/><summary>Evidence</summary></entry></feed>"""
    assert parse_source("github_release", atom, source_url="https://example.com/feed", observed_at=observed)[0].language == "en"
    listing = b"<html><a href='/short'>tiny</a><a href='/news'>Material artificial intelligence announcement</a><a href='/news'>Duplicate material announcement</a></html>"
    items = parse_html_listing(listing, source_url="https://example.com", observed_at=observed)
    assert [item.url for item in items] == ["https://example.com/news"]
    assert parse_source(
        "research_feed", atom, source_url="https://example.com/feed", observed_at=observed
    )[0].external_id == "a"
    assert parse_public_time(None, observed) == observed
    assert parse_public_time("2026-07-14", observed) == "2026-07-14T00:00:00Z"
    with pytest.raises(AdapterError, match="Invalid publication"):
        parse_public_time("not-a-date", observed)
    with pytest.raises(AdapterError, match="Malformed XML"):
        parse_feed(b"not xml", source_url="https://example.com", observed_at=observed)
    with pytest.raises(AdapterError, match="Malformed JSON"):
        parse_json(b"{", source_url="https://example.com", observed_at=observed)
    with pytest.raises(AdapterError, match="item list"):
        parse_json(b'{"items": {"unexpected": true}}', source_url="https://example.com", observed_at=observed)
    with pytest.raises(AdapterError, match="Malformed sitemap"):
        parse_sitemap(b"<", source_url="https://example.com", observed_at=observed)
    with pytest.raises(AdapterError, match="Unsupported"):
        parse_source("unknown", b"", source_url="https://example.com", observed_at=observed)
    with pytest.raises(AdapterError, match="UTF-8"):
        parse_html_listing(b"\xff", source_url="https://example.com", observed_at=observed)


def test_dedicated_source_fixtures_preserve_dates_roles_and_discovery_metadata() -> None:
    fixtures = Path(__file__).parent / "fixtures"
    observed = "2026-07-17T10:00:00Z"
    anthropic = parse_anthropic_newsroom(
        (fixtures / "anthropic_newsroom.html").read_bytes(),
        source_url="https://www.anthropic.com/news",
        observed_at=observed,
    )
    meta = parse_meta_ai_blog(
        (fixtures / "meta_ai_blog.html").read_bytes(),
        source_url="https://ai.meta.com/blog/",
        observed_at=observed,
    )
    cisa = parse_cisa_advisories(
        (fixtures / "cisa_advisories.html").read_bytes(),
        source_url="https://www.cisa.gov/news-events/cybersecurity-advisories",
        observed_at=observed,
    )
    hugging = parse_huggingnews(
        (fixtures / "huggingnews.html").read_bytes(),
        source_url="https://huggingnews.com/",
        observed_at=observed,
    )
    mastodon = parse_mastodon_signal(
        (fixtures / "mastodon_signal.xml").read_bytes(),
        source_url="https://mastodon.social/tags/artificialintelligence.rss",
        observed_at=observed,
    )

    assert [(item.title, item.published_at) for item in anthropic] == [
        ("Introducing Claude Example", "2026-07-17T00:00:00Z")
    ]
    assert meta[0].published_at == "2026-07-16T00:00:00Z"
    assert cisa[0].published_at == "2026-07-15T12:00:00Z"
    assert "Popularity is not evidence" in hugging[0].summary
    assert hugging[0].published_at == "2026-07-17T00:00:00Z"
    assert [item.external_id for item in mastodon] == ["https://social.example/@lab/1"]


def test_huggingnews_documented_json_preserves_event_time_tags_and_public_leads() -> None:
    fixtures = Path(__file__).parent / "fixtures"
    rows = parse_huggingnews_json(
        (fixtures / "huggingnews_latest.json").read_bytes(),
        source_url="https://api.huggingnews.com/api/stories",
        observed_at="2026-07-23T09:00:00Z",
    )
    assert rows[0].external_id == "open-model-release-abc123"
    assert rows[0].published_at == "2026-07-23T07:15:00Z"
    assert rows[0].metadata["aggregator_published_at"] != rows[0].published_at
    assert rows[0].metadata["topic_tags"][0]["slug"] == "ai-open-models"

    enriched = enrich_huggingnews_detail(
        rows[0], (fixtures / "huggingnews_detail.json").read_bytes()
    )
    assert enriched.metadata["reference_summary"] == "Reference-only summary."
    assert len(enriched.metadata["selected_tweets"]) == 1
    assert enriched.metadata["distinct_identity_count"] == 1
    assert enriched.metadata["detail_fetched"] is True
    assert enriched.metadata["short_links"] == ["https://t.co/abc123"]


def test_huggingnews_search_shape_and_missing_fields_degrade_per_item() -> None:
    fixtures = Path(__file__).parent / "fixtures"
    rows = parse_huggingnews_json(
        (fixtures / "huggingnews_search.json").read_bytes(),
        source_url="https://api.huggingnews.com/api/stories?query=open%20models",
        observed_at="2026-07-23T09:00:00Z",
    )
    assert [item.external_id for item in rows] == ["valid-open-model-story-abc123"]
    assert rows[0].published_at == rows[0].metadata["aggregator_published_at"]

    with pytest.raises(AdapterError, match="dayGroups or stories"):
        parse_huggingnews_json(
            b'{"unexpected": []}',
            source_url="https://api.huggingnews.com/api/stories",
            observed_at="2026-07-23T09:00:00Z",
        )


def test_huggingnews_visible_momentum_is_optional_structured_discovery_data() -> None:
    payload = b'''<a class="story-row-link" href="/ai/open-model-release-abc123"><div class="story-row"><div class="story-rank">2</div><div class="story-title">Open model</div><span class="meta-signal">34/25</span></div></a>'''
    assert parse_huggingnews_momentum(payload) == {
        "open-model-release-abc123": {
            "daily_rank": 2,
            "post_count": 34,
            "account_count": 25,
        }
    }
    assert parse_huggingnews_momentum(b"<html>layout changed</html>") == {}


def test_huggingnews_detail_and_optional_momentum_enrichment_are_bounded(
    tmp_path: Path,
) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    synchronize_sources(database)
    fixtures = Path(__file__).parent / "fixtures"
    item = parse_huggingnews_json(
        (fixtures / "huggingnews_latest.json").read_bytes(),
        source_url="https://api.huggingnews.com/api/stories",
        observed_at="2026-07-23T09:00:00Z",
    )[0]
    homepage = b'''<a class="story-row-link" href="/ai/open-model-release-abc123"><div class="story-row"><div class="story-rank">2</div><span class="meta-signal">34/25</span></div></a>'''

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "huggingnews.com":
            return httpx.Response(200, content=homepage, request=request)
        return httpx.Response(
            200,
            content=(fixtures / "huggingnews_detail.json").read_bytes(),
            headers={"content-type": "application/json"},
            request=request,
        )

    with SafeHttpClient(
        allowed_hosts={"api.huggingnews.com", "huggingnews.com"},
        resolver=PUBLIC_IP,
        transport=httpx.MockTransport(handler),
    ) as client:
        collector = Collector(
            database,
            short_link_resolver=lambda _url: "https://publisher.example/report",
        )
        momentum = collector._huggingnews_momentum(client, [item])
        enriched = collector._huggingnews_details(
            client, {"id": "hugging-news"}, momentum
        )

    assert enriched[0].metadata["daily_rank"] == 2
    assert enriched[0].metadata["native_score"] == 84.0
    assert enriched[0].metadata["public_links"] == [
        {"short_url": "https://t.co/abc123", "url": "https://publisher.example/report"}
    ]

    failing = httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("offline", request=request))
    )
    with SafeHttpClient(
        allowed_hosts={"api.huggingnews.com", "huggingnews.com"},
        resolver=PUBLIC_IP,
        transport=failing,
    ) as client:
        assert Collector(database)._huggingnews_momentum(client, [item])[0].metadata.get(
            "native_score"
        ) is None


def test_dedicated_parsers_reject_malformed_or_non_utf8_payloads() -> None:
    observed = "2026-07-17T10:00:00Z"
    for parser, url in (
        (parse_anthropic_newsroom, "https://www.anthropic.com/news"),
        (parse_meta_ai_blog, "https://ai.meta.com/blog/"),
        (parse_cisa_advisories, "https://www.cisa.gov/news-events/cybersecurity-advisories"),
        (parse_huggingnews, "https://huggingnews.com/"),
    ):
        with pytest.raises(AdapterError):
            parser(b"\xff", source_url=url, observed_at=observed)
    with pytest.raises(AdapterError, match="Malformed Mastodon"):
        parse_mastodon_signal(b"<", source_url="https://mastodon.social/tags/artificialintelligence.rss", observed_at=observed)


def test_github_release_headline_is_grounded_in_repository_identity() -> None:
    item = Observation("1", "Patch release: v5.14.1", "https://github.com/huggingface/transformers/releases/tag/v5.14.1", "2026-07-17T00:00:00Z")
    rows = Collector._contextualize_release_titles(
        {"url": "https://github.com/huggingface/transformers/releases.atom", "name": "Transformers Releases"},
        [item],
    )
    assert rows[0].title == "Transformers v5.14.1 released"


def test_hacker_news_engagement_changes_do_not_become_material_updates() -> None:
    first = Observation(
        "hn-1",
        "A material AI announcement",
        "https://example.com/announcement",
        "2026-07-23T08:00:00Z",
        "Article URL: https://example.com/announcement Points: 5 # Comments: 1",
    )
    second = Observation(
        "hn-1",
        "A material AI announcement",
        "https://example.com/announcement",
        "2026-07-23T08:00:00Z",
        "Article URL: https://example.com/announcement Points: 500 # Comments: 99",
    )
    source = {"id": "hacker-news-ai"}
    first_enriched = Collector._contextualize_native_metrics(source, [first])[0]
    second_enriched = Collector._contextualize_native_metrics(source, [second])[0]

    assert first_enriched.content_hash == second_enriched.content_hash
    assert first_enriched.metadata["native_score"] < second_enriched.metadata["native_score"]


def test_huggingnews_public_links_queue_proposals_without_verifying_them(
    tmp_path: Path,
) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    seed_demo_data(database)
    observation = Observation(
        "story-slug",
        "A high-attention AI development",
        "https://huggingnews.com/ai/story-slug",
        "2026-07-23T08:00:00Z",
        metadata={
            "selected_tweets": [],
            "public_links": [
                {
                    "short_url": "https://t.co/abc123",
                    "url": "https://publisher.example/report",
                }
            ],
        },
    )
    with database.transaction() as connection:
        Collector._record_discovery_leads(
            connection,
            "story-demo-watch-003",
            {"id": "huggingnews", "name": "HuggingNews"},
            observation,
            "2026-07-23T09:00:00Z",
        )
        Collector._record_discovery_leads(
            connection,
            "story-demo-watch-003",
            {"id": "huggingnews", "name": "HuggingNews"},
            observation,
            "2026-07-23T09:01:00Z",
        )

    assert database.one(
        "SELECT COUNT(*) AS count FROM discovery_lead WHERE lead_type = 'public_link'"
    ) == {"count": 1}
    assert database.one(
        "SELECT status, proposed_role, acquisition_method FROM evidence_source WHERE canonical_url = ?",
        ("https://publisher.example/report",),
    ) == {
        "status": "queued",
        "proposed_role": "Reporting",
        "acquisition_method": "discovery_enrichment",
    }
    assert database.one(
        "SELECT COUNT(*) AS count FROM work_item WHERE kind = 'evidence_enrichment'"
    ) == {"count": 1}
    assert database.one(
        "SELECT evidence_gate FROM candidate WHERE story_id = 'story-demo-watch-003'"
    ) == {"evidence_gate": 0}


def test_completion_registry_enables_validated_non_sec_sources_and_excludes_sec() -> None:
    payload = json.loads(
        (Path(__file__).parents[2] / "src/open_source_ai_news_wire/definitions/sources.json").read_text(encoding="utf-8")
    )
    definitions = {item["id"]: item for item in payload["sources"]}
    expected = {
        "anthropic-newsroom": "anthropic_newsroom",
        "meta-ai-blog": "meta_ai_blog",
        "cisa-ai-security": "cisa_advisories",
        "hugging-news": "huggingnews_json",
        "mastodon-ai-signal": "mastodon_signal",
    }
    for source_id, adapter in expected.items():
        assert definitions[source_id]["adapter"] == adapter
        if source_id == "cisa-ai-security":
            assert definitions[source_id]["enabled_by_default"] is False
            assert definitions[source_id]["validation_status"] == "validated-unavailable"
        else:
            assert definitions[source_id]["enabled_by_default"] is True
            assert definitions[source_id]["validation_status"] == "live-validated"
    assert definitions["sec-ai-company-filings"]["enabled_by_default"] is False
    assert definitions["sec-ai-company-filings"]["validation_status"] == "excluded-by-operator"


def test_qualification_has_neutral_broader_lane_and_bounded_open_source_lens() -> None:
    observation = Observation(
        "one",
        "AI regulator opens safety and transparency policy consultation",
        "https://example.com/one",
        "2026-07-14T09:30:00Z",
    )
    result = qualify(
        observation,
        {"family": "Government and law", "monitoring_role": "Event"},
        observed_at="2026-07-14T10:00:00Z",
    )
    assert result.lane == "Broader AI News"
    assert result.opportunity_strength == "Moderate"
    assert "audit" in result.mechanism
    assert result.counterargument


def test_collector_helper_boundaries_and_due_calculation(tmp_path: Path) -> None:
    assert _title_tokens("The AI model and benchmark") == {"model", "benchmark"}
    assert _similarity("AI model security launch", "Model security launch") == 1.0
    assert _similarity("AI", "the") == 0.0
    client = _default_client_factory({"base_hosts_json": '["example.com"]'})
    assert client.allowed_hosts == {"example.com"}
    client.close()
    assert Collector._due({"state_last_checked_at": None}, "2026-07-14T10:00:00Z") is True
    assert Collector._due(
        {"state_last_checked_at": "2026-07-14T09:50:00Z", "minimum_interval_minutes": 30},
        "2026-07-14T10:00:00Z",
    ) is False
    assert Collector._due(
        {"state_last_checked_at": "2026-07-14T09:00:00Z", "minimum_interval_minutes": 30},
        "2026-07-14T10:00:00Z",
    ) is True

    database = _database_with_one_source(tmp_path)
    collector = Collector(database, now=lambda: "2026-07-14T10:00:00Z")
    with pytest.raises(ValueError, match="Unsupported"):
        collector.scan(trigger="invalid")
    observation = Observation("1", "AI policy", "https://example.com/1", "2026-07-14T09:00:00Z")
    assert collector._definition_filtered_observations(
        {"definition_json": "invalid"}, [observation]
    ) == [observation]
    assert collector._incremental_observations(
        {"id": "openai-news", "cursor": "invalid"}, [observation], None, None
    ) == [observation]


def test_source_definition_terms_filter_general_feeds_without_ai_false_positives() -> None:
    source = {
        "definition_json": json.dumps(
            {"include_terms": ["AI", "artificial intelligence"], "exclude_terms": ["sponsored"]}
        )
    }
    observations = [
        Observation("1", "Thailand issues an ordinary notice", "https://example.com/1", "2026-07-14T09:00:00Z"),
        Observation("2", "Agency publishes AI safety rule", "https://example.com/2", "2026-07-14T09:00:00Z"),
        Observation("3", "Sponsored artificial intelligence roundup", "https://example.com/3", "2026-07-14T09:00:00Z"),
    ]
    assert [item.external_id for item in Collector._definition_filtered_observations(source, observations)] == ["2"]


def _database_with_one_source(tmp_path: Path) -> Database:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    definitions = json.loads(
        (Path(__file__).parents[2] / "src/open_source_ai_news_wire/definitions/sources.json").read_text(encoding="utf-8")
    )
    overrides = {
        source["id"]: {"enabled_by_default": source["id"] == "openai-news"}
        for source in definitions["sources"]
    }
    (database.paths.config / "sources.local.json").write_text(
        json.dumps({"sources": overrides}), encoding="utf-8"
    )
    synchronize_sources(database)
    return database


def _feed(title: str, description: str = "Public evidence") -> bytes:
    return f"""<rss><channel><item><guid>release-1</guid><title>{title}</title>
    <link>https://openai.com/news/release-1</link>
    <pubDate>Tue, 14 Jul 2026 09:30:00 GMT</pubDate><description>{description}</description>
    </item></channel></rss>""".encode()


def test_collector_is_incremental_and_creates_verified_candidate(tmp_path: Path) -> None:
    database = _database_with_one_source(tmp_path)
    payload = {"body": _feed("Open-source AI model release improves inference security")}

    def factory(source: dict[str, object]) -> SafeHttpClient:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=payload["body"],
                headers={"content-type": "application/rss+xml", "etag": '"v1"'},
                request=request,
            )
        )
        return SafeHttpClient(
            allowed_hosts=set(json.loads(str(source["base_hosts_json"]))),
            resolver=PUBLIC_IP,
            transport=transport,
        )

    collector = Collector(database, client_factory=factory, now=lambda: "2026-07-14T10:00:00Z")
    first = collector.scan(trigger="manual")
    second = collector.scan(trigger="manual")

    assert first.result == "success"
    assert first.discovered_count == 1
    assert second.discovered_count == 0
    assert database.one("SELECT COUNT(*) AS count FROM raw_observation") == {"count": 1}
    story = database.one("SELECT status, priority, lane FROM story_cluster")
    assert story == {"status": "candidate", "priority": "Urgent", "lane": "Open Ecosystem News"}
    assert database.one("SELECT evidence_gate, importance_gate FROM candidate") == {
        "evidence_gate": 1,
        "importance_gate": 1,
    }
    assert database.one("SELECT health FROM source_state WHERE source_id = 'openai-news'") == {"health": "healthy"}

    story_id = database.one("SELECT id FROM story_cluster")["id"]
    database.execute(
        "INSERT INTO draft(story_id,mode,status,version,headline,created_at,updated_at) VALUES(?, 'Neutral News Brief', 'Current', 1, 'Draft', '2026-07-14T10:00:00Z', '2026-07-14T10:00:00Z')",
        (story_id,),
    )
    with database.transaction() as connection:
        similar = collector._find_story(
            connection,
            Observation("similar", "Open source AI model release improves inference security", "https://example.com/similar", "2026-07-14T09:31:00Z"),
        )
    assert similar and similar["id"] == story_id

    payload["body"] = _feed(
        "Open-source AI model release improves inference security",
        "Corrected and materially expanded public evidence",
    )
    changed = collector.scan(trigger="manual")
    assert changed.discovered_count == 1
    assert database.one("SELECT material_update FROM story_cluster") == {"material_update": 1}
    assert database.one("SELECT status FROM draft") == {"status": "Needs Review"}
    assert database.one("SELECT kind FROM alert WHERE kind='correction'") == {"kind": "correction"}


def test_collector_collapses_republished_fingerprint_and_advances_cursor(tmp_path: Path) -> None:
    database = _database_with_one_source(tmp_path)
    title = "Open-source AI model release improves inference security"
    payload = {
        "body": f"""<rss><channel><item><guid>original-item</guid><title>{title}</title>
        <link>https://openai.com/news/original-item</link>
        <pubDate>Tue, 14 Jul 2026 09:30:00 GMT</pubDate>
        <description>Original discovery metadata</description></item></channel></rss>""".encode()
    }

    def factory(source: dict[str, object]) -> SafeHttpClient:
        return SafeHttpClient(
            allowed_hosts=set(json.loads(str(source["base_hosts_json"]))),
            resolver=PUBLIC_IP,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=payload["body"],
                    headers={"content-type": "application/rss+xml"},
                    request=request,
                )
            ),
        )

    collector = Collector(database, client_factory=factory, now=lambda: "2026-07-14T10:00:00Z")
    first = collector.scan(trigger="manual")
    payload["body"] = f"""<rss><channel><item><guid>republished-item</guid><title>{title}</title>
    <link>https://openai.com/news/republished-item</link>
    <pubDate>Tue, 14 Jul 2026 09:45:00 GMT</pubDate>
    <description>Changed popularity metadata from the republished item</description>
    </item></channel></rss>""".encode()
    second = collector.scan(trigger="manual")

    assert first.result == "success"
    assert second.result == "success"
    assert second.discovered_count == 0
    assert database.one("SELECT COUNT(*) AS count FROM raw_observation") == {"count": 1}
    assert database.one("SELECT COUNT(*) AS count FROM story_cluster") == {"count": 1}
    assert json.loads(
        database.one("SELECT cursor FROM source_state WHERE source_id = 'openai-news'")["cursor"]
    )["external_id"] == "republished-item"


def test_deadline_backlog_and_recovered_source_notice(tmp_path: Path) -> None:
    database = _database_with_one_source(tmp_path)
    past_deadline = datetime(2026, 7, 14, 9, tzinfo=UTC)
    collector = Collector(database, now=lambda: "2026-07-14T10:00:00Z")
    result = collector.scan(deadline=past_deadline)
    assert result.result == "degraded"
    assert database.one("SELECT queue_remaining FROM scan_run") == {"queue_remaining": 1}

    database.execute(
        "UPDATE source_state SET health='degraded', failure_streak=3, last_checked_at=NULL WHERE source_id='openai-news'"
    )

    def factory(source: dict[str, object]) -> SafeHttpClient:
        return SafeHttpClient(
            allowed_hosts=set(json.loads(str(source["base_hosts_json"]))), resolver=PUBLIC_IP,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(304, headers={"etag": '"v2"'}, request=request)
            ),
        )

    recovered = Collector(
        database, client_factory=factory, now=lambda: "2026-07-14T10:31:00Z"
    ).scan(trigger="scheduled")
    assert recovered.result == "success"
    assert database.one("SELECT kind FROM alert WHERE kind='recovery'") == {"kind": "recovery"}


def test_whole_mac_offline_does_not_advance_source_failure_streak(tmp_path: Path) -> None:
    database = _database_with_one_source(tmp_path)

    def factory(source: dict[str, object]) -> SafeHttpClient:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline", request=request)

        return SafeHttpClient(
            allowed_hosts=set(json.loads(str(source["base_hosts_json"]))),
            resolver=PUBLIC_IP,
            transport=httpx.MockTransport(handler),
        )

    result = Collector(
        database, client_factory=factory, now=lambda: "2026-07-14T10:00:00Z"
    ).scan(trigger="scheduled")

    assert result.offline is True
    assert database.one("SELECT failure_streak FROM source_state WHERE source_id = 'openai-news'") == {"failure_streak": 0}


def test_dns_failure_is_grouped_once_and_recovery_is_grouped(tmp_path: Path, monkeypatch) -> None:
    database = _database_with_one_source(tmp_path)

    def unavailable(*_args, **_kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "name unavailable")

    monkeypatch.setattr(socket, "getaddrinfo", unavailable)
    first = Collector(database, now=lambda: "2026-07-14T10:00:00Z").scan(trigger="scheduled")
    second = Collector(database, now=lambda: "2026-07-14T10:01:00Z").scan(trigger="scheduled")

    assert first.offline is True and second.offline is True
    assert database.one("SELECT COUNT(*) AS count FROM diagnostic_event WHERE event_type='offline'") == {"count": 1}
    assert database.one("SELECT COUNT(*) AS count FROM alert WHERE title='News Wire is offline'") == {"count": 1}
    assert database.one("SELECT failure_streak FROM source_state WHERE source_id='openai-news'") == {"failure_streak": 0}

    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    transport = httpx.MockTransport(lambda request: httpx.Response(304, request=request))
    factory = lambda source: SafeHttpClient(
        allowed_hosts=set(json.loads(str(source["base_hosts_json"]))),
        resolver=PUBLIC_IP,
        transport=transport,
    )
    recovered = Collector(database, client_factory=factory, now=lambda: "2026-07-14T10:31:00Z").scan()
    assert recovered.result == "success"
    assert database.get_state("network_offline_active") == "false"
    assert database.one("SELECT COUNT(*) AS count FROM alert WHERE title='News Wire is back online'") == {"count": 1}


def test_mixed_network_and_parser_failures_remain_source_specific(tmp_path: Path) -> None:
    database = _database_with_one_source(tmp_path)
    set_source_enabled(database, "deepmind-blog", True)

    def factory(source: dict[str, object]) -> SafeHttpClient:
        if source["id"] == "openai-news":
            def handler(request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("network", request=request)
        else:
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, content=b"not xml", headers={"content-type": "application/xml"}, request=request)
        return SafeHttpClient(
            allowed_hosts=set(json.loads(str(source["base_hosts_json"]))),
            resolver=PUBLIC_IP,
            transport=httpx.MockTransport(handler),
        )

    result = Collector(database, client_factory=factory, now=lambda: "2026-07-14T10:00:00Z").scan()

    assert result.offline is False
    assert result.source_failure_count == 2
    assert database.one("SELECT failure_streak FROM source_state WHERE source_id='openai-news'") == {"failure_streak": 1}


def test_parser_failure_degrades_only_its_source(tmp_path: Path) -> None:
    database = _database_with_one_source(tmp_path)

    def factory(source: dict[str, object]) -> SafeHttpClient:
        return SafeHttpClient(
            allowed_hosts=set(json.loads(str(source["base_hosts_json"]))),
            resolver=PUBLIC_IP,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"not xml", headers={"content-type": "application/xml"}, request=request)
            ),
        )

    result = Collector(
        database, client_factory=factory, now=lambda: "2026-07-14T10:00:00Z"
    ).scan(trigger="manual")

    assert result.result == "degraded"
    assert database.one("SELECT health FROM source_state WHERE source_id = 'openai-news'") == {"health": "degraded"}
    assert database.one("SELECT kind FROM alert") == {"kind": "health"}


def test_discovery_source_creates_watch_only_above_both_thresholds(tmp_path: Path) -> None:
    database = _database_with_one_source(tmp_path)
    override_path = database.paths.config / "sources.local.json"
    overrides = json.loads(override_path.read_text(encoding="utf-8"))
    overrides["sources"]["openai-news"]["monitoring_role"] = "Discovery"
    override_path.write_text(json.dumps(overrides), encoding="utf-8")

    def factory(source: dict[str, object]) -> SafeHttpClient:
        return SafeHttpClient(
            allowed_hosts=set(json.loads(str(source["base_hosts_json"]))),
            resolver=PUBLIC_IP,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=_feed("Breaking AI model security incident and regulation benchmark"),
                    headers={"content-type": "application/rss+xml"},
                    request=request,
                )
            ),
        )

    Collector(database, client_factory=factory, now=lambda: "2026-07-14T10:00:00Z").scan()
    assert database.one("SELECT status, watch_status FROM story_cluster") == {
        "status": "watch", "watch_status": "Active"
    }
    assert database.one("SELECT status FROM watch_notice") == {"status": "active"}


def test_interval_filter_and_critical_storage_gate(tmp_path: Path, monkeypatch) -> None:
    database = _database_with_one_source(tmp_path)
    observations = [
        Observation("old", "AI model security release", "https://example.com/old", "2026-07-01T00:00:00Z"),
        Observation("new", "AI model security release", "https://example.com/new", "2026-07-14T09:00:00Z"),
    ]
    collector = Collector(database, now=lambda: "2026-07-14T10:00:00Z")
    assert [item.external_id for item in collector._incremental_observations(
        {"id": "openai-news", "cursor": None}, observations, "2026-07-14T00:00:00Z", None
    )] == ["new"]

    monkeypatch.setattr(database, "storage_pressure", lambda **_kwargs: SimpleNamespace(level="critical"))
    result = collector.scan()
    assert result.result == "blocked_low_disk"


def test_local_source_enablement_updates_both_compatibility_and_state_tables(tmp_path: Path) -> None:
    database = _database_with_one_source(tmp_path)
    set_source_enabled(database, "openai-news", False)
    assert database.one("SELECT enabled, health FROM source_registry WHERE id = 'openai-news'") == {
        "enabled": 0,
        "health": "paused",
    }
    assert database.one("SELECT enabled, health FROM source_state WHERE source_id = 'openai-news'") == {
        "enabled": 0,
        "health": "paused",
    }
