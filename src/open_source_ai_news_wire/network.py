"""Bounded public-source HTTP client with SSRF and redirect defenses."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx


class UnsafeRequest(ValueError):
    """Raised when a request violates the public-source network boundary."""


class ResponseTooLarge(RuntimeError):
    """Raised when a source exceeds its bounded response allowance."""


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
    for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
        addresses.add(str(entry[4][0]))
    return sorted(addresses)


def _public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


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
        self.maximum_bytes = maximum_bytes
        self.maximum_redirects = maximum_redirects
        timeout = httpx.Timeout(connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout)
        self.client = httpx.Client(
            follow_redirects=False,
            timeout=timeout,
            transport=transport,
            trust_env=False,
            headers={"User-Agent": user_agent, "Accept": "application/atom+xml, application/rss+xml, application/json, application/xml, text/xml, text/html;q=0.8"},
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> SafeHttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _validated(self, url: str) -> str:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.username or parsed.password:
            raise UnsafeRequest("Embedded URL credentials are prohibited")
        if parsed.scheme not in {"https", "http"}:
            raise UnsafeRequest("Only web URLs are allowed")
        if parsed.scheme == "http" and host not in self.allow_http_hosts:
            raise UnsafeRequest("Plain HTTP requires an explicit source exception")
        if not host or host not in self.allowed_hosts:
            raise UnsafeRequest("Host is not registered for this source")
        addresses = list(self.resolver(host))
        if not addresses or any(not _public_address(address) for address in addresses):
            raise UnsafeRequest("Source host did not resolve exclusively to public addresses")
        return url

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        current = self._validated(url)
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        for redirect_count in range(self.maximum_redirects + 1):
            with self.client.stream("GET", current, headers=headers) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    if redirect_count >= self.maximum_redirects:
                        raise UnsafeRequest("Too many source redirects")
                    location = response.headers.get("location")
                    if not location:
                        raise UnsafeRequest("Redirect omitted its destination")
                    current = self._validated(urljoin(current, location))
                    headers = {}
                    continue
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
                    body.extend(chunk)
                    if len(body) > self.maximum_bytes:
                        raise ResponseTooLarge("Source response exceeded the configured byte limit")
                self.client.cookies.clear()
                return FetchResult(current, response.status_code, dict(response.headers), bytes(body))
        raise UnsafeRequest("Redirect processing failed")
