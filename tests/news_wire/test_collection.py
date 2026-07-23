from __future__ import annotations

import json
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import open_source_ai_news_wire.network as network_module

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
    publication_timestamps,
    parse_source,
    parse_sitemap,
)
from open_source_ai_news_wire.collector import Collector, _default_client_factory, _similarity, _title_tokens
from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.demo import seed_demo_data
from open_source_ai_news_wire.network import (
    NetworkDeadlineExceeded,
    ResponseTooLarge,
    SafeHttpClient,
    UnsafeRequest,
    _PinnedTransport,
    _preferred_addresses,
    _public_address,
    _remaining_seconds,
    resolve_known_short_url,
    system_resolver,
)
from open_source_ai_news_wire.qualification import qualify
from open_source_ai_news_wire.source_registry import set_source_enabled, synchronize_sources
from open_source_ai_news_wire.storage import Database


PUBLIC_IP = lambda _host: ["93.184.216.34"]


@pytest.mark.parametrize(
    "address",
    (
        "0.0.0.0",
        "10.0.0.1",
        "100.64.0.1",
        "127.0.0.1",
        "169.254.1.1",
        "172.16.0.1",
        "192.0.0.1",
        "192.0.2.1",
        "192.168.1.1",
        "198.18.0.1",
        "198.51.100.1",
        "203.0.113.1",
        "224.0.0.1",
        "240.0.0.1",
        "::",
        "::1",
        "fc00::1",
        "fe80::1",
        "ff00::1",
        "2001:db8::1",
        "not-an-address",
    ),
)
def test_public_address_rejects_every_private_and_special_range(address: str) -> None:
    assert _public_address(address) is False


def test_pinned_transport_preserves_host_and_tls_name_and_verifies_peer() -> None:
    captured: dict[str, object] = {}

    class NetworkStream:
        def __init__(self, peer: str):
            self.peer = peer

        def get_extra_info(self, name: str):
            return (self.peer, 443) if name == "server_addr" else None

    class RecordingTransport(httpx.BaseTransport):
        def __init__(self, peer: str):
            self.peer = peer

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            captured.update(
                host=request.url.host,
                host_header=request.headers["host"],
                sni=request.extensions["sni_hostname"],
            )
            return httpx.Response(
                200,
                content=b"ok",
                extensions={"network_stream": NetworkStream(self.peer)},
            )

    pinned = _PinnedTransport("example.com", "93.184.216.34")
    pinned._transport.close()
    pinned._transport = RecordingTransport("93.184.216.34")  # type: ignore[assignment]
    response = pinned.handle_request(httpx.Request("GET", "https://example.com/path"))
    response.close()
    assert captured == {
        "host": "93.184.216.34",
        "host_header": "example.com",
        "sni": "example.com",
    }

    pinned._transport = RecordingTransport("93.184.216.35")  # type: ignore[assignment]
    with pytest.raises(UnsafeRequest, match="peer"):
        pinned.handle_request(httpx.Request("GET", "https://example.com/path"))


def test_pinned_transport_rejects_host_credentials_and_malformed_peer() -> None:
    class MalformedPeerTransport(httpx.BaseTransport):
        def handle_request(self, _request: httpx.Request) -> httpx.Response:
            class Stream:
                def get_extra_info(self, _name: str):
                    return (object(),)

            return httpx.Response(
                200, content=b"ok", extensions={"network_stream": Stream()}
            )

    pinned = _PinnedTransport("example.com", "93.184.216.34")
    pinned._transport.close()
    pinned._transport = MalformedPeerTransport()  # type: ignore[assignment]
    with pytest.raises(UnsafeRequest, match="hostname"):
        pinned.handle_request(httpx.Request("GET", "https://other.example/path"))
    with pytest.raises(UnsafeRequest, match="Credential-bearing"):
        pinned.handle_request(
            httpx.Request(
                "GET", "https://example.com/path", headers={"Cookie": "session=secret"}
            )
        )
    with pytest.raises(UnsafeRequest, match="peer"):
        pinned.handle_request(httpx.Request("GET", "https://example.com/path"))
    pinned.close()


def test_system_resolver_and_deadline_helpers_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0)),
        ],
    )
    assert system_resolver("example.com") == [
        "2606:2800:220:1:248:1893:25c8:1946",
        "93.184.216.34",
    ]
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    with pytest.raises(ConnectionError, match="could not be resolved"):
        system_resolver("example.com")
    assert 0 < _remaining_seconds(datetime.now() + timedelta(seconds=1), 5) <= 5


def test_public_address_order_prefers_ipv4_without_dropping_ipv6() -> None:
    assert _preferred_addresses(
        ["2606:2800:220:1:248:1893:25c8:1946", "93.184.216.35", "93.184.216.34"]
    ) == (
        "93.184.216.34",
        "93.184.216.35",
        "2606:2800:220:1:248:1893:25c8:1946",
    )


def test_safe_client_retries_each_validated_pinned_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []

    class AddressTransport(httpx.BaseTransport):
        def __init__(self, _host: str, address: str):
            self.address = address

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            attempts.append(self.address)
            if self.address == "93.184.216.34":
                raise httpx.ConnectError("no route", request=request)
            return httpx.Response(
                200,
                content=b"ok",
                headers={"content-type": "text/plain"},
                request=request,
            )

    monkeypatch.setattr(network_module, "_PinnedTransport", AddressTransport)
    with SafeHttpClient(
        allowed_hosts={"example.com"},
        resolver=lambda _host: [
            "2606:2800:220:1:248:1893:25c8:1946",
            "93.184.216.35",
            "93.184.216.34",
        ],
    ) as client:
        assert client.fetch("https://example.com/feed").body == b"ok"
    assert attempts == ["93.184.216.34", "93.184.216.35"]


def test_safe_client_uses_validated_ipv6_after_ipv4_routes_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []
    ipv6 = "2606:2800:220:1:248:1893:25c8:1946"

    class AddressTransport(httpx.BaseTransport):
        def __init__(self, _host: str, address: str):
            self.address = address

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            attempts.append(self.address)
            if self.address == "93.184.216.34":
                raise httpx.ConnectError("no IPv4 route", request=request)
            return httpx.Response(
                200,
                content=b"ok",
                headers={"content-type": "text/plain"},
                request=request,
            )

    monkeypatch.setattr(network_module, "_PinnedTransport", AddressTransport)
    with SafeHttpClient(
        allowed_hosts={"example.com"},
        resolver=lambda _host: [ipv6, "93.184.216.34"],
    ) as client:
        assert client.fetch("https://example.com/feed").status_code == 200
    assert attempts == ["93.184.216.34", ipv6]


def test_safe_client_raises_after_every_validated_address_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []

    class FailingTransport(httpx.BaseTransport):
        def __init__(self, _host: str, address: str):
            self.address = address

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            attempts.append(self.address)
            raise httpx.ConnectError("unreachable", request=request)

    monkeypatch.setattr(network_module, "_PinnedTransport", FailingTransport)
    with SafeHttpClient(
        allowed_hosts={"example.com"},
        resolver=lambda _host: ["93.184.216.35", "93.184.216.34"],
    ) as client:
        with pytest.raises(httpx.ConnectError, match="unreachable"):
            client.fetch("https://example.com/feed")
    assert attempts == ["93.184.216.34", "93.184.216.35"]


def test_source_connection_attempts_are_bounded_after_full_dns_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []
    public_addresses = [f"93.184.216.{number}" for number in range(1, 14)]

    class FailingTransport(httpx.BaseTransport):
        def __init__(self, _host: str, address: str):
            self.address = address

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            attempts.append(self.address)
            raise httpx.ConnectError("unreachable", request=request)

    monkeypatch.setattr(network_module, "_PinnedTransport", FailingTransport)
    with SafeHttpClient(
        allowed_hosts={"example.com"}, resolver=lambda _host: public_addresses
    ) as client:
        with pytest.raises(UnsafeRequest, match="connection-attempt limit"):
            client.fetch("https://example.com/feed")
    assert attempts == public_addresses[:12]

    attempts.clear()
    with SafeHttpClient(
        allowed_hosts={"example.com"},
        resolver=lambda _host: [*public_addresses, "127.0.0.1"],
    ) as client:
        with pytest.raises(UnsafeRequest, match="exclusively to public"):
            client.fetch("https://example.com/feed")
    assert attempts == []


def test_safe_client_deadline_stops_address_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []
    remaining_checks = 0

    class FailingTransport(httpx.BaseTransport):
        def __init__(self, _host: str, address: str):
            self.address = address

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            attempts.append(self.address)
            raise httpx.ConnectTimeout("unreachable", request=request)

    def bounded_remaining(_deadline: datetime | None, maximum: float) -> float:
        nonlocal remaining_checks
        remaining_checks += 1
        if remaining_checks >= 10:
            raise NetworkDeadlineExceeded("Worker network deadline expired")
        return maximum

    monkeypatch.setattr(network_module, "_PinnedTransport", FailingTransport)
    monkeypatch.setattr(network_module, "_remaining_seconds", bounded_remaining)
    with SafeHttpClient(
        allowed_hosts={"example.com"},
        resolver=lambda _host: ["93.184.216.35", "93.184.216.34"],
    ) as client:
        with pytest.raises(NetworkDeadlineExceeded):
            client.fetch(
                "https://example.com/feed",
                deadline=datetime(2099, 1, 1, tzinfo=UTC),
            )
    assert attempts == ["93.184.216.34"]


def test_safe_client_does_not_retry_after_read_or_policy_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []

    class ReadFailureTransport(httpx.BaseTransport):
        def __init__(self, _host: str, address: str):
            self.address = address

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            attempts.append(self.address)
            raise httpx.ReadTimeout("slow response", request=request)

    monkeypatch.setattr(network_module, "_PinnedTransport", ReadFailureTransport)
    with SafeHttpClient(
        allowed_hosts={"example.com"},
        resolver=lambda _host: ["93.184.216.35", "93.184.216.34"],
    ) as client:
        with pytest.raises(httpx.ReadTimeout):
            client.fetch("https://example.com/feed")
    assert attempts == ["93.184.216.34"]

    attempts.clear()

    class PolicyFailureTransport(httpx.BaseTransport):
        def __init__(self, _host: str, address: str):
            self.address = address

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            attempts.append(self.address)
            return httpx.Response(
                200,
                content=b"not a feed",
                headers={"content-type": "image/png"},
                request=request,
            )

    monkeypatch.setattr(network_module, "_PinnedTransport", PolicyFailureTransport)
    with SafeHttpClient(
        allowed_hosts={"example.com"},
        resolver=lambda _host: ["93.184.216.35", "93.184.216.34"],
    ) as client:
        with pytest.raises(UnsafeRequest, match="content type"):
            client.fetch("https://example.com/feed")
    assert attempts == ["93.184.216.34"]


def test_short_link_retries_each_validated_pinned_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[tuple[str, str]] = []

    class AddressTransport(httpx.BaseTransport):
        def __init__(self, host: str, address: str):
            self.host = host
            self.address = address

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            attempts.append((self.host, self.address))
            if self.address == "93.184.216.34":
                raise httpx.ConnectTimeout("unreachable", request=request)
            if self.host == "t.co":
                return httpx.Response(
                    302,
                    headers={"location": "https://publisher.example/article"},
                    request=request,
                )
            return httpx.Response(200, content=b"ok", request=request)

    monkeypatch.setattr(network_module, "_PinnedTransport", AddressTransport)
    resolved = resolve_known_short_url(
        "https://t.co/abc123",
        resolver=lambda _host: ["93.184.216.35", "93.184.216.34"],
    )
    assert resolved == "https://publisher.example/article"
    assert attempts == [
        ("t.co", "93.184.216.34"),
        ("t.co", "93.184.216.35"),
        ("publisher.example", "93.184.216.34"),
        ("publisher.example", "93.184.216.35"),
    ]


def test_short_link_attempt_cap_spans_redirect_hops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[tuple[str, str]] = []
    public_addresses = [f"93.184.216.{number}" for number in range(1, 8)]

    class AddressTransport(httpx.BaseTransport):
        def __init__(self, host: str, address: str):
            self.host = host
            self.address = address

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            attempts.append((self.host, self.address))
            if self.host == "t.co" and self.address == public_addresses[5]:
                return httpx.Response(
                    302,
                    headers={"location": "https://publisher.example/article"},
                    request=request,
                )
            raise httpx.ConnectError("unreachable", request=request)

    monkeypatch.setattr(network_module, "_PinnedTransport", AddressTransport)
    with pytest.raises(UnsafeRequest, match="connection-attempt limit"):
        resolve_known_short_url(
            "https://t.co/abc123", resolver=lambda _host: public_addresses
        )
    assert len(attempts) == 12
    assert attempts[:6] == [("t.co", address) for address in public_addresses[:6]]
    assert attempts[6:] == [
        ("publisher.example", address) for address in public_addresses[:6]
    ]


def test_safe_client_rejects_unregistered_private_and_credentialed_urls() -> None:
    with SafeHttpClient(allowed_hosts={"example.com"}, resolver=PUBLIC_IP) as client:
        with pytest.raises(UnsafeRequest, match="registered"):
            client.fetch("https://other.example/news")
        with pytest.raises(UnsafeRequest, match="credentials"):
            client.fetch("https://user:pass@example.com/news")
    with SafeHttpClient(allowed_hosts={"example.com"}, resolver=lambda _host: ["127.0.0.1"]) as client:
        with pytest.raises(UnsafeRequest, match="public"):
            client.fetch("https://example.com/news")
    with SafeHttpClient(allowed_hosts={"example.com"}, resolver=lambda _host: ["not-an-ip"]) as client:
        with pytest.raises(UnsafeRequest, match="invalid address"):
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
        with pytest.raises(UnsafeRequest, match="non-standard"):
            client.fetch("https://example.com:8443/feed")
        with pytest.raises(NetworkDeadlineExceeded):
            client.fetch(
                "https://example.com/feed",
                deadline=datetime(2020, 1, 1, tzinfo=UTC),
            )
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


def test_safe_client_rejects_dns_rebinding_between_hops() -> None:
    answers = iter((["93.184.216.34"], ["93.184.216.34"], ["93.184.216.35"]))
    transport = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"location": "/next"}, request=request)
    )
    with SafeHttpClient(
        allowed_hosts={"example.com"}, resolver=lambda _host: next(answers), transport=transport
    ) as client:
        with pytest.raises(UnsafeRequest, match="DNS resolution changed"):
            client.fetch("https://example.com/start")


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


@pytest.mark.parametrize(
    ("url", "message"),
    (
        ("", "missing or too long"),
        ("http://t.co/a", "must use HTTPS"),
        ("https://user:pass@t.co/a", "credentials"),
        ("https://t.co:444/a", "standard HTTPS port"),
    ),
)
def test_known_short_link_rejects_unsafe_initial_urls(url: str, message: str) -> None:
    with pytest.raises(UnsafeRequest, match=message):
        resolve_known_short_url(url, resolver=PUBLIC_IP, transport=httpx.MockTransport(lambda _: None))


def test_known_short_link_rejects_invalid_dns_and_redirect_edges() -> None:
    with pytest.raises(UnsafeRequest, match="invalid address"):
        resolve_known_short_url(
            "https://t.co/a",
            resolver=lambda _host: ["invalid"],
            transport=httpx.MockTransport(lambda _: None),
        )

    for response, message, maximum_redirects in (
        (httpx.Response(302), "omitted", 1),
        (httpx.Response(302, headers={"location": "/again"}), "Too many", 0),
    ):
        with pytest.raises(UnsafeRequest, match=message):
            resolve_known_short_url(
                "https://t.co/a",
                resolver=PUBLIC_IP,
                transport=httpx.MockTransport(
                    lambda request, response=response: httpx.Response(
                        response.status_code, headers=response.headers, request=request
                    )
                ),
                maximum_redirects=maximum_redirects,
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


def test_feed_prefers_published_even_when_updated_appears_first() -> None:
    observed = "2026-07-23T10:00:00Z"
    feed = b"""<feed xmlns='http://www.w3.org/2005/Atom'><entry>
    <id>ordered</id><title>AI model publication timestamp test</title>
    <updated>2026-07-23T09:55:00Z</updated>
    <published>2026-07-22T08:00:00Z</published>
    <link rel='alternate' href='https://example.com/story'/></entry></feed>"""
    item = parse_feed(feed, source_url="https://example.com/feed", observed_at=observed)[0]
    assert item.published_at == "2026-07-22T08:00:00Z"
    assert item.source_reported_at == "2026-07-22T08:00:00Z"
    assert item.timestamp_status == "valid"


def test_publication_timestamp_fallback_states_are_explicit() -> None:
    observed = "2026-07-23T10:00:00Z"
    future = "2026-07-23T10:16:00Z"
    tolerance_boundary = "2026-07-23T10:15:00Z"
    valid = "2026-07-23T09:30:00Z"

    effective, metadata = publication_timestamps(tolerance_boundary, observed)
    assert effective == tolerance_boundary
    assert metadata["timestamp_status"] == "valid"

    effective, metadata = publication_timestamps(future, observed)
    assert effective == observed
    assert metadata["timestamp_status"] == "future_source_fallback_detection"

    effective, metadata = publication_timestamps(
        future, observed, aggregator_value=valid
    )
    assert effective == valid
    assert metadata["timestamp_status"] == "future_source_fallback_aggregator"

    effective, metadata = publication_timestamps(
        valid, observed, aggregator_value=future
    )
    assert effective == valid
    assert metadata["timestamp_status"] == "future_aggregator_ignored"

    effective, metadata = publication_timestamps(
        future, observed, aggregator_value="2026-07-23T10:17:00Z"
    )
    assert effective == observed
    assert metadata["timestamp_status"] == "future_both_fallback_detection"

    effective, metadata = publication_timestamps(
        "not-a-time", observed, aggregator_value=valid
    )
    assert effective == valid
    assert metadata["timestamp_status"] == "malformed_source_fallback_aggregator"
    assert metadata["source_timestamp_raw"] == "not-a-time"


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
    assert [item.external_id for item in rows] == [
        "valid-open-model-story-abc123",
        "invalid-time-story-abc123",
    ]
    assert rows[0].published_at == rows[0].metadata["aggregator_published_at"]
    assert rows[0].timestamp_status == "malformed_source_fallback_aggregator"
    assert rows[1].published_at == "2026-07-23T09:00:00Z"
    assert rows[1].timestamp_status == "malformed_aggregator_fallback_detection"

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


def test_huggingnews_detail_short_link_cap_counts_failed_attempts(
    tmp_path: Path,
) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    item = Observation(
        "story",
        "Open model release",
        "https://huggingnews.com/ai/story",
        "2026-07-23T08:00:00Z",
    )
    attempts: list[str] = []

    def resolve(url: str) -> str:
        attempts.append(url)
        raise UnsafeRequest("blocked")

    detail = json.dumps(
        {
            "slug": "story",
            "title": "Open model release",
            "summary": "Reference-only summary.",
            "selectedTweets": [
                {
                    "authorHandle": "openlab",
                    "url": "https://x.com/openlab/status/123",
                    "tweetedAt": 1784791200000,
                    "text": " ".join(f"https://t.co/link{i}" for i in range(20)),
                }
            ],
        }
    ).encode()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=detail,
            headers={"content-type": "application/json"},
            request=request,
        )
    )
    with SafeHttpClient(
        allowed_hosts={"api.huggingnews.com"},
        resolver=PUBLIC_IP,
        transport=transport,
    ) as client:
        enriched = Collector(database, short_link_resolver=resolve)._huggingnews_details(
            client, {"id": "hugging-news"}, [item]
        )

    assert len(attempts) == 12
    assert enriched[0].metadata["public_links"] == []


def test_huggingnews_detail_loop_stops_at_worker_deadline(tmp_path: Path) -> None:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    item = Observation(
        "story",
        "Open model release",
        "https://huggingnews.com/ai/story",
        "2026-07-23T08:00:00Z",
    )

    class NoFetchClient:
        def fetch(self, _url: str) -> None:
            pytest.fail("detail fetch started after the worker deadline")

    rows = Collector(database)._huggingnews_details(
        NoFetchClient(),  # type: ignore[arg-type]
        {"id": "hugging-news"},
        [item],
        deadline=datetime(2026, 7, 23, 7, 59, tzinfo=UTC),
    )

    assert rows == [item]


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
    assert definitions["arxiv-ai"]["url"].endswith("max_results=50")
    assert definitions["arxiv-ai"]["minimum_interval_minutes"] == 60


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
    assert database.one(
        "SELECT story_revision, material_revision, material_update, material_updated_at FROM story_cluster"
    ) == {
        "story_revision": 2,
        "material_revision": 1,
        "material_update": 0,
        "material_updated_at": None,
    }
    assert database.one("SELECT source_revision FROM source_item") == {
        "source_revision": 2
    }
    assert database.one("SELECT status FROM draft") == {"status": "Needs Review"}
    assert database.one("SELECT kind FROM alert WHERE kind='correction'") == {"kind": "correction"}

    payload["body"] = _feed(
        "Open-source AI model release improves inference safety",
        "Corrected and materially expanded public evidence",
    )
    revised_claim = collector.scan(trigger="manual")
    assert revised_claim.discovered_count == 1
    assert database.one(
        "SELECT story_revision, material_revision, material_update, material_updated_at FROM story_cluster"
    ) == {
        "story_revision": 3,
        "material_revision": 2,
        "material_update": 1,
        "material_updated_at": "2026-07-14T10:00:00Z",
    }
    assert database.one("SELECT text FROM claim") == {
        "text": "Open-source AI model release improves inference safety"
    }


def test_collector_persists_and_deduplicates_future_timestamp_fallback(
    tmp_path: Path,
) -> None:
    database = _database_with_one_source(tmp_path)
    body = b"""<rss><channel><item><guid>future-one</guid>
    <title>Open-source AI model release improves inference security</title>
    <link>https://openai.com/news/future-one</link>
    <pubDate>Thu, 23 Jul 2026 10:30:00 GMT</pubDate>
    <description>Public evidence</description></item></channel></rss>"""

    def factory(source: dict[str, object]) -> SafeHttpClient:
        return SafeHttpClient(
            allowed_hosts=set(json.loads(str(source["base_hosts_json"]))),
            resolver=PUBLIC_IP,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=body,
                    headers={"content-type": "application/rss+xml"},
                    request=request,
                )
            ),
        )

    collector = Collector(
        database, client_factory=factory, now=lambda: "2026-07-23T10:00:00Z"
    )
    collector.scan(trigger="manual")
    collector.scan(trigger="manual")

    expected = {
        "source_reported_at": "2026-07-23T10:30:00Z",
        "aggregator_published_at": None,
        "effective_published_at": "2026-07-23T10:00:00Z",
        "timestamp_status": "future_source_fallback_detection",
    }
    assert database.one(
        "SELECT source_reported_at, aggregator_published_at, effective_published_at, timestamp_status FROM raw_observation"
    ) == expected
    assert database.one(
        "SELECT source_reported_at, aggregator_published_at, effective_published_at, timestamp_status FROM source_item"
    ) == expected
    assert database.one(
        "SELECT source_reported_at, aggregator_published_at, effective_published_at, timestamp_status FROM source_revision"
    ) == expected
    assert database.one(
        "SELECT COUNT(*) AS count FROM diagnostic_event WHERE event_type = 'publication_timestamp_fallback'"
    ) == {"count": 1}


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


def test_deadline_during_fetch_becomes_backlog_without_source_failure(
    tmp_path: Path,
) -> None:
    database = _database_with_one_source(tmp_path)
    database.execute(
        "UPDATE source_state SET health='healthy', failure_streak=2 WHERE source_id='openai-news'"
    )

    class DeadlineClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def fetch(self, *_args: object, **_kwargs: object) -> FetchResult:
            raise NetworkDeadlineExceeded("Worker network deadline expired")

    result = Collector(
        database,
        client_factory=lambda _source: DeadlineClient(),  # type: ignore[arg-type]
        now=lambda: "2026-07-14T10:00:00Z",
    ).scan(deadline=datetime(2099, 7, 14, 10, 1, tzinfo=UTC))

    assert result.result == "degraded"
    assert database.one("SELECT queue_remaining FROM scan_run") == {"queue_remaining": 1}
    assert database.one(
        "SELECT status, error_class FROM source_transaction"
    ) == {"status": "deadline", "error_class": "NetworkDeadlineExceeded"}
    assert database.one(
        "SELECT health, failure_streak FROM source_state WHERE source_id='openai-news'"
    ) == {"health": "healthy", "failure_streak": 2}


def test_connection_failure_before_deadline_backlog_is_not_whole_mac_offline(
    tmp_path: Path,
) -> None:
    database = _database_with_one_source(tmp_path)
    set_source_enabled(database, "anthropic-newsroom", True)

    class ErrorClient:
        def __init__(self, source_id: str):
            self.source_id = source_id

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def fetch(self, url: str, **_kwargs: object) -> FetchResult:
            if self.source_id == "anthropic-newsroom":
                raise httpx.ConnectError(
                    "unreachable", request=httpx.Request("GET", url)
                )
            raise NetworkDeadlineExceeded("Worker network deadline expired")

    result = Collector(
        database,
        client_factory=lambda source: ErrorClient(str(source["id"])),  # type: ignore[arg-type]
        now=lambda: "2026-07-14T10:00:00Z",
    ).scan(deadline=datetime(2099, 7, 14, 10, 1, tzinfo=UTC))

    assert result.result == "degraded"
    assert result.offline is False
    assert database.one("SELECT queue_remaining FROM scan_run") == {"queue_remaining": 1}
    assert database.query(
        "SELECT source_id, status FROM source_transaction ORDER BY id"
    ) == [
        {"source_id": "anthropic-newsroom", "status": "failed"},
        {"source_id": "openai-news", "status": "deadline"},
    ]
    assert database.one(
        "SELECT failure_streak FROM source_state WHERE source_id='anthropic-newsroom'"
    ) == {"failure_streak": 1}
    assert database.one(
        "SELECT COUNT(*) AS count FROM diagnostic_event WHERE event_type='offline'"
    ) == {"count": 0}
    assert database.get_state("network_offline_active") == ""


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
