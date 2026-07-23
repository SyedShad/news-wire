"""Short-lived, fail-closed CONNECT broker for packet-only assistance.

The broker deliberately does not terminate TLS.  Codex therefore performs the
normal certificate and hostname verification while this process controls DNS,
the exact upstream peer, and the small set of destinations it may reach.
"""

from __future__ import annotations

import ipaddress
import select
import socket
import socketserver
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


BROKER_POLICY_VERSION = "connect-v2"
REVIEWED_CODEX_HOSTS = frozenset(
    {
        "chatgpt.com",
        "auth.openai.com",
        "api.openai.com",
        # The Apple-signed ChatGPT-bundled Codex client selects one of these
        # exact regional OpenAI service hosts for account-backed inference.
        # Keep this list explicit: parent-domain and wildcard access are not
        # permitted by the broker policy.
        "sdmntprcentralus.oaiusercontent.com",
        "sdmntprnorthcentralus.oaiusercontent.com",
        "sdmntprsoutheastus3.oaiusercontent.com",
        "sdmntprsouthcentralus.oaiusercontent.com",
        "sdmntprwestus3.oaiusercontent.com",
    }
)
_MAX_CONNECT_HEADER = 8192
_MAX_RESOLVED_ADDRESSES = 12
_READ_SIZE = 64 * 1024


class BrokerError(RuntimeError):
    """A safe, externally reportable broker failure."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class ConnectRequest:
    host: str
    port: int
    remainder: bytes = b""


def _split_authority(value: str) -> tuple[str, int]:
    if (
        not value
        or any(character.isspace() for character in value)
        or "@" in value
        or "/" in value
        or "?" in value
        or "#" in value
    ):
        raise BrokerError("broker_connect_malformed", "CONNECT authority is invalid")
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0 or value[closing + 1 :] != ":443":
            raise BrokerError("broker_connect_malformed", "CONNECT authority is invalid")
        host = value[1:closing]
        port_text = "443"
    else:
        if value.count(":") != 1:
            raise BrokerError("broker_connect_malformed", "CONNECT authority is invalid")
        host, port_text = value.rsplit(":", 1)
    if not host or port_text != "443":
        raise BrokerError("broker_connect_port_blocked", "only port 443 is permitted")
    try:
        host_ascii = host.encode("idna").decode("ascii").casefold().rstrip(".")
    except UnicodeError as error:
        raise BrokerError("broker_connect_malformed", "CONNECT host is invalid") from error
    if not host_ascii or len(host_ascii) > 253:
        raise BrokerError("broker_connect_malformed", "CONNECT host is invalid")
    return host_ascii, 443


def parse_connect_request(
    data: bytes,
    *,
    allowed_hosts: Iterable[str] = REVIEWED_CODEX_HOSTS,
) -> ConnectRequest:
    """Parse one strict CONNECT prelude and reject credentials and ambiguity."""
    if len(data) > _MAX_CONNECT_HEADER and b"\r\n\r\n" not in data[:_MAX_CONNECT_HEADER]:
        raise BrokerError("broker_connect_malformed", "CONNECT headers are too large")
    boundary = data.find(b"\r\n\r\n")
    if boundary < 0 or boundary + 4 > _MAX_CONNECT_HEADER:
        raise BrokerError("broker_connect_malformed", "CONNECT headers are incomplete")
    header_bytes = data[:boundary]
    if b"\x00" in header_bytes or b"\n " in header_bytes or b"\n\t" in header_bytes:
        raise BrokerError("broker_connect_malformed", "CONNECT headers are invalid")
    try:
        lines = header_bytes.decode("ascii").split("\r\n")
    except UnicodeDecodeError as error:
        raise BrokerError("broker_connect_malformed", "CONNECT headers are not ASCII") from error
    if not lines or len(lines[0].split(" ")) != 3:
        raise BrokerError("broker_connect_malformed", "CONNECT request line is invalid")
    method, authority, protocol = lines[0].split(" ")
    if method != "CONNECT" or protocol != "HTTP/1.1":
        raise BrokerError("broker_connect_malformed", "only HTTP/1.1 CONNECT is accepted")
    host, port = _split_authority(authority)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise BrokerError("broker_ip_literal_blocked", "IP literal destinations are prohibited")

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line or ":" not in line:
            raise BrokerError("broker_connect_malformed", "CONNECT header is invalid")
        name, value = line.split(":", 1)
        lowered = name.strip().casefold()
        clean_value = value.strip()
        if (
            not lowered
            or lowered in headers
            or any(ord(character) < 33 or ord(character) > 126 for character in name)
            or "\r" in clean_value
            or "\n" in clean_value
        ):
            raise BrokerError("broker_connect_malformed", "CONNECT header is invalid")
        if lowered in {"authorization", "proxy-authorization", "cookie", "set-cookie"}:
            raise BrokerError("broker_credentials_blocked", "credentials are not accepted by the broker")
        headers[lowered] = clean_value
    if "host" not in headers:
        raise BrokerError("broker_connect_malformed", "Host header is required")
    header_host, header_port = _split_authority(headers["host"])
    if (header_host, header_port) != (host, port):
        raise BrokerError("broker_connect_malformed", "Host header does not match CONNECT authority")
    reviewed = {item.casefold().rstrip(".") for item in allowed_hosts}
    if host not in reviewed:
        raise BrokerError("broker_host_blocked", "destination host is not reviewed")
    return ConnectRequest(host=host, port=port, remainder=data[boundary + 4 :])


def validate_public_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Return a globally routable address; reject every private/special range."""
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError as error:
        raise BrokerError("broker_dns_unsafe", "DNS returned an invalid address") from error
    if (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise BrokerError("broker_dns_unsafe", "DNS returned a private or special address")
    return address


Resolver = Callable[..., list[tuple[int, int, int, str, tuple[Any, ...]]]]


def resolve_public_addresses(
    host: str,
    port: int = 443,
    *,
    resolver: Resolver = socket.getaddrinfo,
) -> tuple[tuple[int, tuple[Any, ...], str], ...]:
    """Resolve once, validate the complete result, and return numeric peers."""
    try:
        results = resolver(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError as error:
        raise BrokerError("broker_dns_failed", "reviewed host could not be resolved") from error
    if not results or len(results) > _MAX_RESOLVED_ADDRESSES:
        raise BrokerError("broker_dns_failed", "DNS returned an unsafe result count")
    resolved: list[tuple[int, tuple[Any, ...], str]] = []
    seen: set[tuple[int, str]] = set()
    for family, socket_type, protocol, _canonical, sockaddr in results:
        if (
            family not in {socket.AF_INET, socket.AF_INET6}
            or socket_type != socket.SOCK_STREAM
            or protocol not in {0, socket.IPPROTO_TCP}
            or not sockaddr
        ):
            raise BrokerError("broker_dns_unsafe", "DNS returned an unsupported address")
        address = validate_public_address(str(sockaddr[0]))
        key = (family, address.compressed)
        if key in seen:
            continue
        seen.add(key)
        numeric = (
            (address.compressed, port)
            if family == socket.AF_INET
            else (address.compressed, port, 0, 0)
        )
        resolved.append((family, numeric, address.compressed))
    if not resolved:
        raise BrokerError("broker_dns_failed", "DNS returned no usable public addresses")
    return tuple(resolved)


Connector = Callable[[int, tuple[Any, ...], float], socket.socket]


def _connect_numeric(family: int, sockaddr: tuple[Any, ...], timeout: float) -> socket.socket:
    upstream = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    upstream.settimeout(timeout)
    try:
        upstream.connect(sockaddr)
    except BaseException:
        upstream.close()
        raise
    return upstream


def connect_pinned_peer(
    addresses: tuple[tuple[int, tuple[Any, ...], str], ...],
    *,
    connector: Connector = _connect_numeric,
    timeout: float = 15.0,
) -> socket.socket:
    """Connect to a numeric result and prove the kernel peer matches it exactly."""
    last_error: OSError | None = None
    for family, sockaddr, expected in addresses:
        try:
            upstream = connector(family, sockaddr, timeout)
        except OSError as error:
            last_error = error
            continue
        try:
            peer = validate_public_address(str(upstream.getpeername()[0])).compressed
            if peer != expected:
                raise BrokerError("broker_peer_mismatch", "connected peer did not match pinned DNS result")
            upstream.settimeout(None)
            return upstream
        except BaseException:
            upstream.close()
            raise
    raise BrokerError("broker_upstream_unavailable", "reviewed host was unreachable") from last_error


def _receive_prelude(connection: socket.socket) -> bytes:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        if len(data) >= _MAX_CONNECT_HEADER:
            raise BrokerError("broker_connect_malformed", "CONNECT headers are too large")
        chunk = connection.recv(min(4096, _MAX_CONNECT_HEADER - len(data)))
        if not chunk:
            raise BrokerError("broker_connect_malformed", "CONNECT request ended early")
        data.extend(chunk)
    return bytes(data)


class _BrokerServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True
    block_on_close = False

    def __init__(self, address: tuple[str, int], broker: "ConnectBroker"):
        self.broker = broker
        super().__init__(address, _ConnectHandler, bind_and_activate=True)


class _ConnectHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        broker: ConnectBroker = self.server.broker  # type: ignore[attr-defined]
        client: socket.socket = self.request
        upstream: socket.socket | None = None
        broker._track(client)
        try:
            request = parse_connect_request(
                _receive_prelude(client), allowed_hosts=broker.allowed_hosts
            )
            addresses = resolve_public_addresses(
                request.host, request.port, resolver=broker.resolver
            )
            upstream = connect_pinned_peer(
                addresses, connector=broker.connector, timeout=broker.connect_timeout
            )
            broker._track(upstream)
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if request.remainder:
                upstream.sendall(request.remainder)
            broker._relay(client, upstream)
        except BrokerError as error:
            broker._record_failure(error.code)
            try:
                client.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass
        except OSError:
            broker._record_failure("broker_transport_failed")
        finally:
            if upstream is not None:
                broker._untrack(upstream)
                upstream.close()
            broker._untrack(client)


class ConnectBroker:
    """A loopback-only CONNECT broker whose lifetime matches one invocation."""

    def __init__(
        self,
        *,
        allowed_hosts: Iterable[str] = REVIEWED_CODEX_HOSTS,
        resolver: Resolver = socket.getaddrinfo,
        connector: Connector = _connect_numeric,
        connect_timeout: float = 15.0,
        idle_timeout: float = 300.0,
    ):
        self.allowed_hosts = frozenset(item.casefold().rstrip(".") for item in allowed_hosts)
        if not self.allowed_hosts or any(not item for item in self.allowed_hosts):
            raise BrokerError("broker_policy_invalid", "reviewed host allowlist is invalid")
        self.resolver = resolver
        self.connector = connector
        self.connect_timeout = max(0.1, float(connect_timeout))
        self.idle_timeout = max(1.0, float(idle_timeout))
        self._server: _BrokerServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._sockets: set[socket.socket] = set()
        self._failure_code = ""

    @property
    def endpoint(self) -> tuple[str, int]:
        if self._server is None:
            raise BrokerError("broker_not_running", "CONNECT broker is not running")
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    @property
    def proxy_url(self) -> str:
        host, port = self.endpoint
        return f"http://{host}:{port}"

    @property
    def failure_code(self) -> str:
        with self._lock:
            return self._failure_code

    def start(self) -> "ConnectBroker":
        if self._server is not None:
            raise BrokerError("broker_already_running", "CONNECT broker is already running")
        try:
            self._server = _BrokerServer(("127.0.0.1", 0), self)
        except OSError as error:
            raise BrokerError("broker_start_failed", "CONNECT broker could not bind loopback") from error
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="news-wire-connect-broker",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        with self._lock:
            sockets = tuple(self._sockets)
            self._sockets.clear()
        for connection in sockets:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        if thread is not None:
            thread.join(timeout=5)

    def __enter__(self) -> "ConnectBroker":
        return self.start()

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.stop()

    def _record_failure(self, code: str) -> None:
        with self._lock:
            if not self._failure_code:
                self._failure_code = code

    def _track(self, connection: socket.socket) -> None:
        with self._lock:
            self._sockets.add(connection)

    def _untrack(self, connection: socket.socket) -> None:
        with self._lock:
            self._sockets.discard(connection)

    def _relay(self, client: socket.socket, upstream: socket.socket) -> None:
        sockets = (client, upstream)
        while self._server is not None:
            readable, _, _ = select.select(sockets, (), (), self.idle_timeout)
            if not readable:
                raise BrokerError("broker_idle_timeout", "CONNECT tunnel was idle")
            for source in readable:
                data = source.recv(_READ_SIZE)
                if not data:
                    return
                destination = upstream if source is client else client
                destination.sendall(data)
