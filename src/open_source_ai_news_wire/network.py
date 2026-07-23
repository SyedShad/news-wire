"""Bounded public-source HTTP client with SSRF and redirect defenses."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx


class UnsafeRequest(ValueError):
    """Raised when a request violates the public-source network boundary."""


class ResponseTooLarge(RuntimeError):
    """Raised when a source exceeds its bounded response allowance."""


class NetworkUnavailable(ConnectionError):
    """Raised when the host network cannot currently resolve a public source."""


class NetworkDeadlineExceeded(TimeoutError):
    """Raised before network work that would start after the worker deadline."""


_MAX_PINNED_CONNECTION_ATTEMPTS = 12


def _validated_public_https_url(value: str) -> tuple[str, str]:
    if not value or len(value) > 2048:
        raise UnsafeRequest("Public link is missing or too long")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or not host:
        raise UnsafeRequest("Public links must use HTTPS")
    if parsed.username or parsed.password:
        raise UnsafeRequest("Embedded URL credentials are prohibited")
    if parsed.port not in {None, 443}:
        raise UnsafeRequest("Public links must use the standard HTTPS port")
    normalized = urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))
    return normalized, host


Resolver = Callable[[str], Iterable[str]]


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    status_code: int
    headers: dict[str, str]
    body: bytes
    not_modified: bool = False


def system_resolver(host: str) -> list[str]:
    addresses: set[str] = set()
    try:
        for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
            addresses.add(str(entry[4][0]))
    except OSError as error:
        raise NetworkUnavailable("The source host could not be resolved") from error
    return sorted(addresses)


def _preferred_addresses(addresses: Iterable[str]) -> tuple[str, ...]:
    """Prefer IPv4 on hosts where this Mac has no working IPv6 route.

    Every returned address has already passed the public-address gate.  The
    ordering only controls which independently pinned connection is attempted
    first; it does not relax DNS or peer verification.
    """
    parsed = {ipaddress.ip_address(value) for value in addresses}
    return tuple(str(address) for address in sorted(parsed, key=lambda item: (item.version, item.packed)))


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_private
    )


def _remaining_seconds(deadline: datetime | None, maximum: float) -> float:
    if deadline is None:
        return maximum
    normalized = deadline if deadline.tzinfo else deadline.replace(tzinfo=UTC)
    remaining = (normalized.astimezone(UTC) - datetime.now(UTC)).total_seconds()
    if remaining <= 0:
        raise NetworkDeadlineExceeded("Worker network deadline expired")
    return min(maximum, remaining)


class _PinnedTransport(httpx.BaseTransport):
    """Dial one validated IP while retaining the HTTPS hostname for TLS."""

    def __init__(self, host: str, address: str):
        self.host = host
        self.address = str(ipaddress.ip_address(address))
        self._transport = httpx.HTTPTransport(verify=True, trust_env=False, retries=0)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        request_host = (request.url.host or "").lower().rstrip(".")
        if request_host != self.host:
            raise UnsafeRequest("Pinned transport hostname changed unexpectedly")
        for prohibited in ("authorization", "proxy-authorization", "cookie"):
            if prohibited in request.headers:
                raise UnsafeRequest("Credential-bearing request headers are prohibited")
        headers = httpx.Headers(request.headers)
        headers["Host"] = self.host
        extensions = dict(request.extensions)
        extensions["sni_hostname"] = self.host
        pinned_request = httpx.Request(
            request.method,
            request.url.copy_with(host=self.address),
            headers=headers,
            stream=request.stream,
            extensions=extensions,
        )
        response = self._transport.handle_request(pinned_request)
        network_stream = response.extensions.get("network_stream")
        peer = (
            network_stream.get_extra_info("server_addr")
            if network_stream is not None
            else None
        )
        try:
            peer_address = str(ipaddress.ip_address(peer[0])) if peer else ""
        except (ValueError, TypeError, IndexError):
            peer_address = ""
        if peer_address != self.address:
            response.close()
            raise UnsafeRequest("HTTPS peer did not match the validated DNS pin")
        return response

    def close(self) -> None:
        self._transport.close()


class SafeHttpClient:
    def __init__(
        self,
        *,
        allowed_hosts: Iterable[str],
        resolver: Resolver = system_resolver,
        transport: httpx.BaseTransport | None = None,
        connect_timeout: float = 10,
        read_timeout: float = 20,
        maximum_bytes: int = 5_000_000,
        user_agent: str = "OpenSourceAINewsWire/0.2 (+local viability pilot)",
        allow_http_hosts: Iterable[str] = (),
        maximum_redirects: int = 3,
    ):
        self.allowed_hosts = {host.lower().rstrip(".") for host in allowed_hosts}
        self.allow_http_hosts = {host.lower().rstrip(".") for host in allow_http_hosts}
        self.resolver = resolver
        self._resolved_addresses: dict[str, tuple[str, ...]] = {}
        self.maximum_bytes = maximum_bytes
        self.maximum_redirects = maximum_redirects
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.timeout = httpx.Timeout(connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout)
        self._headers = {"User-Agent": user_agent, "Accept": "application/atom+xml, application/rss+xml, application/json, application/xml, text/xml, text/html;q=0.8"}
        self.client = httpx.Client(
            follow_redirects=False,
            timeout=self.timeout,
            transport=transport,
            trust_env=False,
            headers=self._headers,
        )
        self._injected_transport = transport is not None

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> SafeHttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _validated(
        self, url: str, *, deadline: datetime | None = None
    ) -> tuple[str, str, tuple[str, ...]]:
        _remaining_seconds(deadline, self.connect_timeout)
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.username or parsed.password:
            raise UnsafeRequest("Embedded URL credentials are prohibited")
        if parsed.scheme not in {"https", "http"}:
            raise UnsafeRequest("Only web URLs are allowed")
        if parsed.scheme == "http" and host not in self.allow_http_hosts:
            raise UnsafeRequest("Plain HTTP requires an explicit source exception")
        if parsed.port not in {None, 443 if parsed.scheme == "https" else 80}:
            raise UnsafeRequest("Source URL uses a non-standard web port")
        if not host or host not in self.allowed_hosts:
            raise UnsafeRequest("Host is not registered for this source")
        try:
            addresses = _preferred_addresses(self.resolver(host))
        except ValueError as error:
            raise UnsafeRequest("Source DNS returned an invalid address") from error
        _remaining_seconds(deadline, self.connect_timeout)
        if not addresses or any(not _public_address(address) for address in addresses):
            raise UnsafeRequest("Source host did not resolve exclusively to public addresses")
        previous = self._resolved_addresses.get(host)
        if previous is not None and previous != addresses:
            raise UnsafeRequest("Source DNS resolution changed during one request")
        self._resolved_addresses[host] = addresses
        return url, host, addresses

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        deadline: datetime | None = None,
    ) -> FetchResult:
        current, _, _ = self._validated(url, deadline=deadline)
        headers: dict[str, str] = {}
        connection_attempts = 0
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        for redirect_count in range(self.maximum_redirects + 1):
            # Revalidate immediately before every connection and redirect hop.
            current, host, addresses = self._validated(current, deadline=deadline)
            _remaining_seconds(deadline, self.connect_timeout)
            redirect_target: str | None = None
            candidates: tuple[str | None, ...] = (
                (None,) if self._injected_transport else tuple(addresses)
            )
            for address_index, address in enumerate(candidates):
                if connection_attempts >= _MAX_PINNED_CONNECTION_ATTEMPTS:
                    raise UnsafeRequest("Source connection-attempt limit exceeded")
                connection_attempts += 1
                temporary_client: httpx.Client | None = None
                client = self.client
                if address is not None:
                    temporary_client = httpx.Client(
                        follow_redirects=False,
                        timeout=self.timeout,
                        transport=_PinnedTransport(host, address),
                        trust_env=False,
                        headers=self._headers,
                    )
                    client = temporary_client
                client.cookies.clear()
                try:
                    timeout = httpx.Timeout(
                        connect=_remaining_seconds(deadline, self.connect_timeout),
                        read=_remaining_seconds(deadline, self.read_timeout),
                        write=_remaining_seconds(deadline, self.read_timeout),
                        pool=_remaining_seconds(deadline, self.connect_timeout),
                    )
                    with client.stream("GET", current, headers=headers, timeout=timeout) as response:
                        client.cookies.clear()
                        _remaining_seconds(deadline, self.read_timeout)
                        if response.status_code in {301, 302, 303, 307, 308}:
                            if redirect_count >= self.maximum_redirects:
                                raise UnsafeRequest("Too many source redirects")
                            location = response.headers.get("location")
                            if not location:
                                raise UnsafeRequest("Redirect omitted its destination")
                            redirect_target, _, _ = self._validated(
                                urljoin(current, location), deadline=deadline
                            )
                            headers = {}
                            break
                        if response.status_code == 304:
                            return FetchResult(current, 304, dict(response.headers), b"", True)
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").lower()
                        if content_type and not any(
                            allowed in content_type
                            for allowed in ("xml", "json", "html", "text/plain", "application/octet-stream")
                        ):
                            raise UnsafeRequest(f"Unsupported source content type: {content_type}")
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            _remaining_seconds(deadline, self.read_timeout)
                            body.extend(chunk)
                            if len(body) > self.maximum_bytes:
                                raise ResponseTooLarge("Source response exceeded the configured byte limit")
                        client.cookies.clear()
                        return FetchResult(current, response.status_code, dict(response.headers), bytes(body))
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    if address_index + 1 >= len(candidates):
                        raise
                finally:
                    if temporary_client is not None:
                        temporary_client.close()
            if redirect_target is not None:
                current = redirect_target
                continue
        raise UnsafeRequest("Redirect processing failed")


def resolve_known_short_url(
    url: str,
    *,
    short_hosts: Iterable[str] = ("t.co",),
    resolver: Resolver = system_resolver,
    transport: httpx.BaseTransport | None = None,
    maximum_redirects: int = 4,
    deadline: datetime | None = None,
) -> str:
    """Resolve an allowlisted public short URL without relaxing source fetch rules."""
    current, initial_host = _validated_public_https_url(url)
    allowed_short_hosts = {host.lower().rstrip(".") for host in short_hosts}
    if initial_host not in allowed_short_hosts:
        raise UnsafeRequest("Only known public short-link hosts may be resolved")

    resolved_addresses: dict[str, tuple[str, ...]] = {}

    def validate(value: str) -> str:
        _remaining_seconds(deadline, 8)
        normalized, host = _validated_public_https_url(value)
        try:
            addresses = _preferred_addresses(resolver(host))
        except ValueError as error:
            raise UnsafeRequest("Short-link DNS returned an invalid address") from error
        _remaining_seconds(deadline, 8)
        if not addresses or any(not _public_address(address) for address in addresses):
            raise UnsafeRequest("Short-link hop did not resolve exclusively to public addresses")
        previous = resolved_addresses.get(host)
        if previous is not None and previous != addresses:
            raise UnsafeRequest("Short-link DNS resolution changed during one request")
        resolved_addresses[host] = addresses
        return normalized

    headers = {
        "User-Agent": "OpenSourceAINewsWire/0.3.7 (+local viability pilot)",
        "Accept": "text/html, application/xhtml+xml;q=0.8, */*;q=0.1",
    }
    injected_client = (
        httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(connect=8, read=10, write=10, pool=8),
            transport=transport,
            trust_env=False,
            headers=headers,
        )
        if transport is not None
        else None
    )
    try:
        connection_attempts = 0
        for redirect_count in range(maximum_redirects + 1):
            current = validate(current)
            host = (urlsplit(current).hostname or "").lower().rstrip(".")
            addresses = resolved_addresses[host]
            redirect_target: str | None = None
            candidates: tuple[str | None, ...] = (
                (None,) if injected_client is not None else tuple(addresses)
            )
            for address_index, address in enumerate(candidates):
                if connection_attempts >= _MAX_PINNED_CONNECTION_ATTEMPTS:
                    raise UnsafeRequest("Short-link connection-attempt limit exceeded")
                connection_attempts += 1
                temporary_client: httpx.Client | None = None
                client = injected_client
                if client is None:
                    assert address is not None
                    temporary_client = httpx.Client(
                        follow_redirects=False,
                        timeout=httpx.Timeout(connect=8, read=10, write=10, pool=8),
                        transport=_PinnedTransport(host, address),
                        trust_env=False,
                        headers=headers,
                    )
                    client = temporary_client
                client.cookies.clear()
                try:
                    timeout = httpx.Timeout(
                        connect=_remaining_seconds(deadline, 8),
                        read=_remaining_seconds(deadline, 10),
                        write=_remaining_seconds(deadline, 10),
                        pool=_remaining_seconds(deadline, 8),
                    )
                    with client.stream("GET", current, timeout=timeout) as response:
                        client.cookies.clear()
                        _remaining_seconds(deadline, 10)
                        if response.status_code in {301, 302, 303, 307, 308}:
                            if redirect_count >= maximum_redirects:
                                raise UnsafeRequest("Too many short-link redirects")
                            location = response.headers.get("location")
                            if not location:
                                raise UnsafeRequest("Short-link redirect omitted its destination")
                            redirect_target = validate(urljoin(current, location))
                            break
                        response.raise_for_status()
                        return current
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    if address_index + 1 >= len(candidates):
                        raise
                finally:
                    if temporary_client is not None:
                        temporary_client.close()
            if redirect_target is not None:
                current = redirect_target
                continue
    finally:
        if injected_client is not None:
            injected_client.close()
    raise UnsafeRequest("Short-link redirect processing failed")
