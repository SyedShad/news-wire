from __future__ import annotations

import json
import socket
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from open_source_ai_news_wire import assistance, assistance_broker as broker_module
from open_source_ai_news_wire.assistance import (
    AssistanceConfigurationError,
    CodexIdentity,
    CodexInvoker,
    assistance_isolation_current,
    run_isolation_canary,
    sandbox_profile,
    verify_codex_identity,
)
from open_source_ai_news_wire.assistance_broker import (
    BrokerError,
    connect_pinned_peer,
    parse_connect_request,
    resolve_public_addresses,
    validate_public_address,
)
from open_source_ai_news_wire.config import resolve_runtime_paths
from open_source_ai_news_wire.storage import Database


def _database(tmp_path: Path) -> Database:
    database = Database(resolve_runtime_paths(tmp_path / "wire-data"))
    database.initialize()
    return database


def _identity(path: str = "/Applications/ChatGPT.app/Contents/Resources/codex") -> CodexIdentity:
    return CodexIdentity(
        path=path,
        version="reviewed-test-version",
        team_id="2DC432GLL2",
        identifier="codex",
        sha256="a" * 64,
        cdhash="b" * 40,
    )


def _connect(host: str = "chatgpt.com", *, headers: str = "") -> bytes:
    return (
        f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n{headers}\r\n"
    ).encode("ascii")


def test_connect_parser_accepts_only_exact_reviewed_hosts() -> None:
    request = parse_connect_request(_connect())
    assert (request.host, request.port) == ("chatgpt.com", 443)
    regional = parse_connect_request(
        _connect("sdmntprcentralus.oaiusercontent.com")
    )
    assert (regional.host, regional.port) == (
        "sdmntprcentralus.oaiusercontent.com",
        443,
    )

    cases = (
        (b"GET / HTTP/1.1\r\nHost: chatgpt.com\r\n\r\n", "malformed"),
        (_connect("example.com"), "host_blocked"),
        (_connect("ab.chatgpt.com"), "host_blocked"),
        (_connect("unexpected.oaiusercontent.com"), "host_blocked"),
        (_connect("127.0.0.1"), "ip_literal"),
        (_connect("[::1]"), "ip_literal"),
        (b"CONNECT chatgpt.com:80 HTTP/1.1\r\nHost: chatgpt.com:80\r\n\r\n", "port_blocked"),
        (_connect(headers="Proxy-Authorization: Basic obvious-dummy\r\n"), "credentials_blocked"),
        (b"CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: auth.openai.com:443\r\n\r\n", "malformed"),
        (b"CONNECT user@chatgpt.com:443 HTTP/1.1\r\nHost: user@chatgpt.com:443\r\n\r\n", "malformed"),
        (b"CONNECT chatgpt.com:443 HTTP/1.0\r\nHost: chatgpt.com:443\r\n\r\n", "malformed"),
        (b"CONNECT chatgpt.com:443 HTTP/1.1\nHost: chatgpt.com:443\n\n", "malformed"),
    )
    for raw, code in cases:
        with pytest.raises(BrokerError, match=code):
            parse_connect_request(raw)


@pytest.mark.parametrize(
    "address",
    (
        "0.0.0.0",
        "10.0.0.1",
        "100.64.0.1",
        "127.0.0.1",
        "169.254.1.1",
        "192.0.2.1",
        "224.0.0.1",
        "255.255.255.255",
        "::",
        "::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
        "2001:db8::1",
    ),
)
def test_private_and_special_addresses_are_rejected(address: str) -> None:
    with pytest.raises(BrokerError, match="dns_unsafe"):
        validate_public_address(address)


def test_dns_result_set_fails_closed_if_any_answer_is_unsafe() -> None:
    def resolver(*_args, **_kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443)),
        ]

    with pytest.raises(BrokerError, match="dns_unsafe"):
        resolve_public_addresses("chatgpt.com", resolver=resolver)


def test_exact_peer_mismatch_is_rejected() -> None:
    class Peer:
        def getpeername(self):
            return ("93.184.216.35", 443)

        def settimeout(self, _timeout):
            return None

        def close(self):
            return None

    with pytest.raises(BrokerError, match="peer_mismatch"):
        connect_pinned_peer(
            ((socket.AF_INET, ("93.184.216.34", 443), "93.184.216.34"),),
            connector=lambda *_args: Peer(),  # type: ignore[arg-type]
        )


def test_outer_profile_denies_direct_network_and_unix_sockets(tmp_path: Path) -> None:
    profile = sandbox_profile(
        tmp_path / "task",
        Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        tmp_path / "credentials" / "auth.json",
        43123,
    )
    assert '(deny default)' in profile
    assert "(deny process-fork)" in profile
    assert '(allow network-outbound (remote tcp "localhost:43123"))' in profile
    assert "*:443" not in profile
    assert "network-inbound" not in profile
    assert "network*" not in profile
    assert "unix" not in profile


@pytest.mark.skipif(
    not Path("/usr/bin/sandbox-exec").is_file(),
    reason="macOS sandbox-exec is unavailable",
)
def test_real_boundary_canary_denies_child_credentials_and_unix_sockets() -> None:
    passed, reason = assistance._run_sandbox_boundary_canaries()
    if reason == "sandbox_boundary_canary_unavailable":
        pytest.skip("the outer test sandbox prohibits nested sandbox application")
    assert passed, reason


def test_failed_boundary_canary_invalidates_isolation_without_invoking_model(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)

    class Invoker:
        identity = _identity()
        private_network_isolation_proven = True

        def invoke(self, _packet):
            pytest.fail("model must not run after a failed local boundary canary")

    class Server:
        server_address = ("127.0.0.1", 43123)

        def __init__(self, *_args):
            pass

        def serve_forever(self):
            return None

        def shutdown(self):
            return None

        def server_close(self):
            return None

    assert not run_isolation_canary(
        database,
        Invoker(),  # type: ignore[arg-type]
        server_factory=Server,
        boundary_canary=lambda: (False, "unix_socket_isolation_failed"),
    )
    assert database.get_state("assistance_isolation_gate") == "failed"
    assert database.get_state("assistance_enabled") == "false"
    assert database.get_state("assistance_unavailable_reason") == (
        "unix_socket_isolation_failed"
    )


def test_binary_identity_checks_signature_team_version_hash_and_cdhash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = tmp_path / "ChatGPT.app" / "Contents" / "Resources" / "codex"
    codex.parent.mkdir(parents=True)
    codex.write_bytes(b"reviewed-binary")
    codex.chmod(0o755)
    digest = assistance.hashlib.sha256(b"reviewed-binary").hexdigest()
    monkeypatch.setattr(assistance, "_known_codex_binary_paths", lambda: (codex,))
    monkeypatch.setattr(assistance, "EXPECTED_CODEX_VERSION", "codex-cli test")
    monkeypatch.setattr(assistance, "EXPECTED_CODEX_SHA256", digest)
    monkeypatch.setattr(assistance, "EXPECTED_CODEX_CDHASH", "b" * 40)

    def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        if "--verify" in arguments:
            return subprocess.CompletedProcess(arguments, 0, "", "valid on disk")
        if "-dvvv" in arguments:
            details = (
                "Identifier=codex\nTeamIdentifier=2DC432GLL2\n"
                f"CDHash={'b' * 40}\n"
            )
            return subprocess.CompletedProcess(arguments, 0, "", details)
        if "--requirements" in arguments:
            requirement = (
                'designated => identifier codex and anchor apple generic and '
                'certificate leaf[subject.OU] = "2DC432GLL2"'
            )
            return subprocess.CompletedProcess(arguments, 0, "", requirement)
        return subprocess.CompletedProcess(arguments, 0, "codex-cli test\n", "")

    identity = verify_codex_identity(codex, command_runner=runner)
    assert identity.team_id == "2DC432GLL2"
    assert identity.sha256 == digest
    assert identity.cdhash == "b" * 40

    monkeypatch.setattr(assistance, "EXPECTED_CODEX_SHA256", "0" * 64)
    with pytest.raises(AssistanceConfigurationError, match="identity_drift"):
        verify_codex_identity(codex, command_runner=runner)


def test_release_attestation_rejects_expiry_policy_and_binary_drift(tmp_path: Path) -> None:
    database = _database(tmp_path)
    issued = datetime(2026, 7, 23, 0, 0, tzinfo=UTC)
    identity = _identity()
    attestation = assistance._attestation_payload(identity, issued)
    timestamp = issued.isoformat().replace("+00:00", "Z")
    database.set_state("assistance_isolation_gate", "passed", timestamp)
    database.set_state("assistance_isolation_version", assistance.ISOLATION_CANARY_VERSION, timestamp)
    database.set_state(
        assistance.ISOLATION_ATTESTATION_STATE,
        Database.json(attestation),
        timestamp,
    )
    assert assistance_isolation_current(
        database, now=issued + timedelta(hours=23), identity=identity
    )
    assert not assistance_isolation_current(
        database, now=issued + timedelta(hours=24), identity=identity
    )
    assert not assistance_isolation_current(
        database,
        now=issued + timedelta(hours=1),
        identity=CodexIdentity(**{**identity.as_dict(), "sha256": "0" * 64}),
    )

    drifted = dict(attestation)
    drifted["release_version"] = "0.3.6"
    fingerprint_payload = dict(drifted)
    fingerprint_payload.pop("fingerprint")
    drifted["fingerprint"] = assistance.hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    database.set_state(
        assistance.ISOLATION_ATTESTATION_STATE, Database.json(drifted), timestamp
    )
    assert not assistance_isolation_current(
        database, now=issued + timedelta(hours=1), identity=identity
    )


def test_automatic_canary_attempts_at_most_once_per_rolling_day(tmp_path: Path) -> None:
    database = _database(tmp_path)
    identity = _identity()

    class Invoker:
        private_network_isolation_proven = True

        def __init__(self):
            self.identity = identity
            self.calls = 0

        def invoke(self, packet):
            self.calls += 1
            return assistance.InvocationResult(
                {
                    "schema_version": 1,
                    "operation": packet["operation"],
                    "supported_claim_ids": [],
                    "headline": "",
                    "factual_brief": "",
                    "lens": "",
                    "notes": "blocked",
                },
                "test",
                1,
                1,
            )

    class Server:
        server_address = ("127.0.0.1", 43123)

        def __init__(self, *_args):
            pass

        def serve_forever(self):
            return None

        def shutdown(self):
            return None

        def server_close(self):
            return None

    invoker = Invoker()
    issued = datetime(2026, 7, 23, 0, 0, tzinfo=UTC)
    assert run_isolation_canary(
        database,
        invoker,
        server_factory=Server,
        automatic=True,
        now=issued,
        boundary_canary=lambda: (True, ""),
    )
    assert run_isolation_canary(
        database,
        invoker,
        server_factory=Server,
        automatic=True,
        now=issued + timedelta(hours=1),
        boundary_canary=lambda: (True, ""),
    )
    assert invoker.calls == 1
    assert database.one(
        "SELECT COUNT(*) AS count FROM usage_ledger WHERE operation='isolation_canary'"
    ) == {"count": 1}


def test_invoker_always_cleans_up_broker_after_failure(tmp_path: Path) -> None:
    codex = tmp_path / "codex"
    codex.write_text("binary", encoding="utf-8")
    codex.chmod(0o755)
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {"access_token": "obvious-dummy"},
                "OPENAI_API_KEY": None,
            }
        ),
        encoding="utf-8",
    )

    class Broker:
        endpoint = ("127.0.0.1", 43123)
        proxy_url = "http://127.0.0.1:43123"
        failure_code = ""
        stopped = False

        def start(self):
            return self

        def stop(self):
            self.stopped = True

    broker = Broker()
    invoker = CodexInvoker(
        codex_binary=codex,
        auth_file=auth,
        runner=lambda *_args: (_ for _ in ()).throw(subprocess.TimeoutExpired("codex", 1)),
        identity_verifier=lambda path: _identity(str(path)),
        broker_factory=lambda: broker,  # type: ignore[arg-type]
    )
    with pytest.raises(assistance.AssistanceTransientError, match="codex_timeout"):
        invoker.invoke({"operation": "triage", "claims": []})
    assert broker.stopped is True


def test_api_key_or_non_chatgpt_auth_is_rejected_without_invocation(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(
            {
                "auth_mode": "apikey",
                "tokens": {"access_token": "obvious-dummy"},
                "OPENAI_API_KEY": "sk-obvious-dummy",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AssistanceConfigurationError, match="ChatGPT-only"):
        assistance._load_saved_chatgpt_auth(auth)


@pytest.mark.parametrize(
    ("raw", "code"),
    (
        (b"X" * 8193, "malformed"),
        (b"CONNECT chatgpt.com:443 HTTP/1.1\r\nBad\r\n\r\n", "malformed"),
        (b"CONNECT chatgpt.com:443 HTTP/1.1\r\n\r\n", "malformed"),
        (
            b"CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\nHost: chatgpt.com:443\r\n\r\n",
            "malformed",
        ),
        (
            b"CONNECT chatgpt.com:443 HTTP/1.1\r\nHo\x01st: chatgpt.com:443\r\n\r\n",
            "malformed",
        ),
        (
            b"CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\r\n Folded: no\r\n\r\n",
            "malformed",
        ),
        (
            b"CONNECT chatgpt.com:443 HTTP/1.1\r\nHost: chatgpt.com:443\xff\r\n\r\n",
            "malformed",
        ),
        (b"CONNECT chatgpt.com HTTP/1.1\r\nHost: chatgpt.com:443\r\n\r\n", "malformed"),
        (b"CONNECT :443 HTTP/1.1\r\nHost: :443\r\n\r\n", "port_blocked"),
        (b"CONNECT [::1:443 HTTP/1.1\r\nHost: [::1:443\r\n\r\n", "malformed"),
    ),
)
def test_connect_parser_rejects_additional_ambiguous_forms(raw: bytes, code: str) -> None:
    with pytest.raises(BrokerError, match=code):
        parse_connect_request(raw)


def test_connect_parser_canonicalizes_host_and_preserves_tls_remainder() -> None:
    request = parse_connect_request(
        b"CONNECT CHATGPT.COM.:443 HTTP/1.1\r\nHost: CHATGPT.COM.:443\r\n\r\nTLS"
    )
    assert request.host == "chatgpt.com"
    assert request.remainder == b"TLS"


def test_authority_rejects_encoding_and_length_edges() -> None:
    class BadHost(str):
        def encode(self, *_args, **_kwargs):
            raise UnicodeError("bad idna")

    class BadAuthority(str):
        def rsplit(self, *_args, **_kwargs):
            return BadHost("chatgpt.com"), "443"

    with pytest.raises(BrokerError, match="malformed"):
        broker_module._split_authority(BadAuthority("chatgpt.com:443"))
    with pytest.raises(BrokerError, match="malformed"):
        broker_module._split_authority("a" * 254 + ":443")


def test_resolver_handles_errors_duplicates_ipv6_and_unsupported_answers() -> None:
    with pytest.raises(BrokerError, match="dns_failed"):
        resolve_public_addresses(
            "chatgpt.com", resolver=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError())
        )
    for answers in ([], [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))] * 13):
        with pytest.raises(BrokerError, match="dns_failed"):
            resolve_public_addresses(
                "chatgpt.com", resolver=lambda *_args, _answers=answers, **_kwargs: _answers
            )
    unsupported = (
        (socket.AF_UNIX, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("x",)),
        (socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_UDP, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ()),
    )
    for answer in unsupported:
        with pytest.raises(BrokerError, match="dns_unsafe"):
            resolve_public_addresses(
                "chatgpt.com", resolver=lambda *_args, _answer=answer, **_kwargs: [_answer]
            )

    answers = [
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443)),
        (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("2606:4700::6810:85e5", 443, 0, 2)),
    ]
    resolved = resolve_public_addresses(
        "chatgpt.com", resolver=lambda *_args, **_kwargs: answers
    )
    assert resolved == (
        (socket.AF_INET, ("93.184.216.34", 443), "93.184.216.34"),
        (socket.AF_INET6, ("2606:4700::6810:85e5", 443, 0, 0), "2606:4700::6810:85e5"),
    )


def test_numeric_connector_and_pinned_peer_retry_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    class NumericSocket:
        def __init__(self, *_args):
            self.closed = False
            self.timeout = None

        def settimeout(self, timeout):
            self.timeout = timeout

        def connect(self, _sockaddr):
            if self.timeout == 1:
                raise OSError("blocked")

        def close(self):
            self.closed = True

    sockets: list[NumericSocket] = []

    def factory(*args):
        value = NumericSocket(*args)
        sockets.append(value)
        return value

    monkeypatch.setattr(broker_module.socket, "socket", factory)
    assert broker_module._connect_numeric(
        socket.AF_INET, ("93.184.216.34", 443), 2
    ).timeout == 2
    with pytest.raises(OSError):
        broker_module._connect_numeric(socket.AF_INET, ("93.184.216.34", 443), 1)
    assert sockets[-1].closed is True

    class Peer:
        def __init__(self):
            self.timeout = 10
            self.closed = False

        def getpeername(self):
            return ("93.184.216.34", 443)

        def settimeout(self, value):
            self.timeout = value

        def close(self):
            self.closed = True

    calls = 0

    def retrying_connector(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("first address unavailable")
        return Peer()

    addresses = (
        (socket.AF_INET, ("1.1.1.1", 443), "1.1.1.1"),
        (socket.AF_INET, ("93.184.216.34", 443), "93.184.216.34"),
    )
    peer = connect_pinned_peer(addresses, connector=retrying_connector)
    assert peer.timeout is None
    assert calls == 2
    with pytest.raises(BrokerError, match="upstream_unavailable"):
        connect_pinned_peer(
            addresses,
            connector=lambda *_args: (_ for _ in ()).throw(OSError("down")),
        )


def test_receive_prelude_handles_chunks_eof_and_limit() -> None:
    reader, writer = socket.socketpair()
    try:
        writer.sendall(b"CONNECT chatgpt.com:443 HTTP/1.1\r\n")
        writer.sendall(b"Host: chatgpt.com:443\r\n\r\nTLS")
        assert broker_module._receive_prelude(reader).endswith(b"TLS")
    finally:
        reader.close()
        writer.close()

    reader, writer = socket.socketpair()
    writer.close()
    try:
        with pytest.raises(BrokerError, match="ended early"):
            broker_module._receive_prelude(reader)
    finally:
        reader.close()

    class Full:
        def recv(self, size):
            return b"X" * size

    with pytest.raises(BrokerError, match="too large"):
        broker_module._receive_prelude(Full())  # type: ignore[arg-type]


class _PinnedSocket:
    def __init__(self, value: socket.socket, peer: str = "93.184.216.34"):
        self.value = value
        self.peer = peer
        self.closed = False

    def fileno(self):
        return self.value.fileno()

    def getpeername(self):
        return (self.peer, 443)

    def settimeout(self, value):
        self.value.settimeout(value)

    def recv(self, size):
        return self.value.recv(size)

    def sendall(self, data):
        return self.value.sendall(data)

    def shutdown(self, how):
        return self.value.shutdown(how)

    def close(self):
        self.closed = True
        return self.value.close()


def _direct_handler(client: object, broker: broker_module.ConnectBroker) -> None:
    handler = object.__new__(broker_module._ConnectHandler)
    handler.request = client
    handler.server = SimpleNamespace(broker=broker)
    handler.handle()


def test_connect_handler_relays_tls_both_directions_without_listener() -> None:
    client, caller = socket.socketpair()
    upstream_socket, upstream_peer = socket.socketpair()
    pinned = _PinnedSocket(upstream_socket)
    answers = [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))
    ]
    broker = broker_module.ConnectBroker(
        resolver=lambda *_args, **_kwargs: answers,
        connector=lambda *_args: pinned,  # type: ignore[arg-type]
        idle_timeout=2,
    )
    broker._server = SimpleNamespace()
    caller.sendall(_connect() + b"TLS-HELLO")
    thread = threading.Thread(target=_direct_handler, args=(client, broker))
    thread.start()
    caller.settimeout(2)
    assert caller.recv(128).startswith(b"HTTP/1.1 200")
    upstream_peer.settimeout(2)
    assert upstream_peer.recv(128) == b"TLS-HELLO"
    upstream_peer.sendall(b"TLS-REPLY")
    assert caller.recv(128) == b"TLS-REPLY"
    caller.close()
    thread.join(timeout=2)
    client.close()
    upstream_peer.close()
    assert not thread.is_alive()
    assert pinned.closed is True
    assert broker._sockets == set()


def test_connect_handler_records_policy_transport_and_response_failures() -> None:
    broker = broker_module.ConnectBroker()
    client, caller = socket.socketpair()
    caller.sendall(b"BAD\r\n\r\n")
    caller.shutdown(socket.SHUT_WR)
    _direct_handler(client, broker)
    assert caller.recv(256).startswith(b"HTTP/1.1 403")
    assert broker.failure_code == "broker_connect_malformed"
    caller.close()
    client.close()

    class BrokenTransport:
        def recv(self, _size):
            raise OSError("read failed")

    transport_broker = broker_module.ConnectBroker()
    _direct_handler(BrokenTransport(), transport_broker)
    assert transport_broker.failure_code == "broker_transport_failed"

    class BrokenResponse:
        def recv(self, _size):
            return b"BAD\r\n\r\n"

        def sendall(self, _data):
            raise OSError("closed")

    response_broker = broker_module.ConnectBroker()
    _direct_handler(BrokenResponse(), response_broker)
    assert response_broker.failure_code == "broker_connect_malformed"


def test_connect_handler_closes_upstream_when_relay_fails() -> None:
    client, caller = socket.socketpair()
    upstream_socket, upstream_peer = socket.socketpair()
    pinned = _PinnedSocket(upstream_socket)
    answers = [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))
    ]
    broker = broker_module.ConnectBroker(
        resolver=lambda *_args, **_kwargs: answers,
        connector=lambda *_args: pinned,  # type: ignore[arg-type]
    )
    broker._relay = lambda *_args: (_ for _ in ()).throw(
        BrokerError("broker_idle_timeout", "idle")
    )
    caller.sendall(_connect())
    _direct_handler(client, broker)
    assert broker.failure_code == "broker_idle_timeout"
    assert pinned.closed is True
    client.close()
    caller.close()
    upstream_peer.close()


def test_broker_lifecycle_and_state_use_fake_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(BrokerError, match="policy_invalid"):
        broker_module.ConnectBroker(allowed_hosts=[])
    with pytest.raises(BrokerError, match="policy_invalid"):
        broker_module.ConnectBroker(allowed_hosts=[""])
    broker = broker_module.ConnectBroker(
        allowed_hosts=["CHATGPT.COM."], connect_timeout=0, idle_timeout=0
    )
    assert broker.allowed_hosts == frozenset({"chatgpt.com"})
    assert broker.connect_timeout == 0.1
    assert broker.idle_timeout == 1.0
    with pytest.raises(BrokerError, match="not_running"):
        _ = broker.endpoint

    class Server:
        server_address = ("127.0.0.1", 43210)

        def __init__(self, _address, owner):
            self.broker = owner
            self.served = False
            self.shutdown_called = False
            self.closed = False

        def serve_forever(self):
            self.served = True

        def shutdown(self):
            self.shutdown_called = True

        def server_close(self):
            self.closed = True

    server_holder: list[Server] = []

    def server_factory(address, owner):
        server = Server(address, owner)
        server_holder.append(server)
        return server

    monkeypatch.setattr(broker_module, "_BrokerServer", server_factory)
    assert broker.start() is broker
    assert broker.endpoint == ("127.0.0.1", 43210)
    assert broker.proxy_url == "http://127.0.0.1:43210"
    with pytest.raises(BrokerError, match="already_running"):
        broker.start()
    tracked, peer = socket.socketpair()
    broker._track(tracked)
    broker._record_failure("first")
    broker._record_failure("second")
    assert broker.failure_code == "first"
    broker.stop()
    peer.close()
    assert server_holder[0].shutdown_called is True
    assert server_holder[0].closed is True
    assert tracked.fileno() == -1
    broker.stop()


def test_broker_context_start_failure_and_relay_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        broker_module,
        "_BrokerServer",
        lambda *_args: (_ for _ in ()).throw(OSError("bind denied")),
    )
    with pytest.raises(BrokerError, match="start_failed"):
        broker_module.ConnectBroker().start()

    broker = broker_module.ConnectBroker()
    broker._server = object()
    monkeypatch.setattr(broker_module.select, "select", lambda *_args: ([], [], []))
    with pytest.raises(BrokerError, match="idle_timeout"):
        broker._relay(object(), object())  # type: ignore[arg-type]
    broker._server = None
    assert broker._relay(object(), object()) is None  # type: ignore[arg-type]

    events: list[str] = []
    monkeypatch.setattr(
        broker_module.ConnectBroker,
        "start",
        lambda self: events.append("start") or self,
    )
    monkeypatch.setattr(
        broker_module.ConnectBroker,
        "stop",
        lambda self: events.append("stop"),
    )
    with broker_module.ConnectBroker():
        events.append("body")
    assert events == ["start", "body", "stop"]


def test_boundary_canary_unit_seams_cover_success_and_fail_closed_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Accepted:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class Listener:
        def __init__(self, *, accept_connection=False):
            self.accept_connection = accept_connection
            self.closed = False

        def bind(self, _path):
            return None

        def listen(self, _count):
            return None

        def settimeout(self, _timeout):
            return None

        def accept(self):
            if self.accept_connection:
                return Accepted(), None
            raise TimeoutError

        def close(self):
            self.closed = True

    ok = subprocess.CompletedProcess([], 1, "", "denied")
    nested = subprocess.CompletedProcess(
        [], 71, "", "sandbox_apply: Operation not permitted"
    )
    leaked = subprocess.CompletedProcess(
        [], 1, "WIRE_CHILD_CREDENTIAL_CANARY_" + "a" * 48, ""
    )
    allowed = subprocess.CompletedProcess([], 0, "", "")

    def scenario(
        *,
        child=ok,
        unix=ok,
        system="Darwin",
        nc=True,
        accept=False,
        socket_error=False,
    ):
        with monkeypatch.context() as scoped:
            scoped.setattr(assistance.platform, "system", lambda: system)
            scoped.setattr(assistance.secrets, "token_hex", lambda _size: "a" * 48)
            scoped.setattr(
                Path,
                "is_file",
                lambda path: str(path) != "/usr/bin/nc" or nc,
            )
            outcomes = iter((child, unix))

            def run(*_args, **_kwargs):
                outcome = next(outcomes)
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome

            scoped.setattr(assistance.subprocess, "run", run)
            if socket_error:
                scoped.setattr(
                    assistance.socket,
                    "socket",
                    lambda *_args: (_ for _ in ()).throw(OSError("socket denied")),
                )
            else:
                scoped.setattr(
                    assistance.socket,
                    "socket",
                    lambda *_args: Listener(accept_connection=accept),
                )
            return assistance._run_sandbox_boundary_canaries(
                sandbox_binary=Path("/fake/sandbox-exec")
            )

    assert scenario() == (True, "")
    assert scenario(system="Linux") == (False, "sandbox_boundary_canary_unavailable")
    assert scenario(child=nested) == (False, "sandbox_boundary_canary_unavailable")
    assert scenario(child=leaked) == (False, "child_credential_isolation_failed")
    assert scenario(child=allowed) == (False, "child_credential_isolation_failed")
    assert scenario(nc=False) == (False, "unix_socket_canary_unavailable")
    assert scenario(unix=subprocess.TimeoutExpired("nc", 3)) == (
        False,
        "unix_socket_isolation_failed",
    )
    assert scenario(unix=nested) == (False, "sandbox_boundary_canary_unavailable")
    assert scenario(accept=True) == (False, "unix_socket_isolation_failed")
    assert scenario(unix=allowed) == (False, "unix_socket_isolation_failed")
    assert scenario(socket_error=True) == (False, "sandbox_boundary_canary_failed")


def test_canary_identity_automatic_state_budget_and_environment_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = _database(tmp_path / "identity")

    class NoIdentity:
        pass

    assert run_isolation_canary(database, NoIdentity()) is False  # type: ignore[arg-type]
    assert database.get_state("assistance_unavailable_reason") == "codex_identity_unverified"

    identity = _identity()

    class Invoker:
        private_network_isolation_proven = True

        def __init__(self):
            self.identity = identity
            self.calls = 0

        def invoke(self, packet):
            self.calls += 1
            return assistance.InvocationResult(
                {
                    "schema_version": 1,
                    "operation": packet["operation"],
                    "supported_claim_ids": [],
                    "headline": "",
                    "factual_brief": "",
                    "lens": "",
                    "notes": "blocked",
                },
                "test",
                1,
                1,
            )

    current = datetime(2026, 7, 24, tzinfo=UTC)
    corrupt = _database(tmp_path / "corrupt")
    corrupt.set_state("assistance_isolation_automatic_attempt_at", "bad", "bad")
    assert not run_isolation_canary(corrupt, Invoker(), automatic=True, now=current)

    budget = _database(tmp_path / "budget")
    budget.set_state("background_unit_limit", "0", current.isoformat())
    assert not run_isolation_canary(budget, Invoker(), automatic=True, now=current)

    class Server:
        server_address = ("127.0.0.1", 43123)

        def __init__(self, *_args):
            pass

        def serve_forever(self):
            return None

        def shutdown(self):
            return None

        def server_close(self):
            return None

    previous = "existing-value"
    monkeypatch.setenv("WIRE_CANARY_CREDENTIAL", previous)
    successful = _database(tmp_path / "successful")
    assert run_isolation_canary(
        successful,
        Invoker(),
        server_factory=Server,
        now=current,
        boundary_canary=lambda: (True, ""),
    )
    assert assistance.os.environ["WIRE_CANARY_CREDENTIAL"] == previous


def test_status_and_safe_error_classification_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = _database(tmp_path)
    monkeypatch.setattr(
        assistance,
        "verify_codex_identity",
        lambda: (_ for _ in ()).throw(
            AssistanceConfigurationError("codex_signature_invalid: no")
        ),
    )
    monkeypatch.setattr(
        assistance,
        "_load_saved_chatgpt_auth",
        lambda _path: (_ for _ in ()).throw(
            AssistanceConfigurationError("codex_authentication_unavailable: no")
        ),
    )
    status = assistance.assistance_status(database)
    assert status["codex_available"] is False
    assert status["login_available"] is False
    assert status["failure_reason"] == "codex_signature_invalid"

    assert assistance._error_code(assistance.ApprovalInvalidated("changed")) == (
        "approval_invalidated"
    )
    assert assistance._error_code(assistance.AssistanceDeferred("later")) == (
        "assistance_waiting"
    )
    assert assistance._error_code(assistance.AssistanceTransientError("later")) == (
        "temporary_assistance_failure"
    )
    assert assistance._error_code(AssistanceConfigurationError("bad")) == (
        "assistance_configuration_error"
    )
    assert assistance._error_code(assistance.AssistanceError("bad")) == (
        "assistance_validation_error"
    )
    assert assistance._error_code(ValueError("bad")) == "unexpected_error"


def test_invoker_rechecks_identity_and_classifies_broker_failures(tmp_path: Path) -> None:
    codex = tmp_path / "codex"
    codex.write_text("binary", encoding="utf-8")
    codex.chmod(0o755)
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {"access_token": "obvious-dummy"},
                "OPENAI_API_KEY": None,
            }
        ),
        encoding="utf-8",
    )
    initial = _identity(str(codex.resolve()))
    identities = iter(
        (initial, CodexIdentity(**{**initial.as_dict(), "sha256": "0" * 64}))
    )
    invoker = CodexInvoker(
        codex_binary=codex,
        auth_file=auth,
        identity_verifier=lambda _path: next(identities),
        broker_factory=lambda: pytest.fail("broker must not start after identity drift"),
    )
    with pytest.raises(AssistanceConfigurationError, match="identity_drift"):
        invoker.invoke({"operation": "triage", "claims": []})

    class Broker:
        endpoint = ("127.0.0.1", 43123)
        proxy_url = "http://127.0.0.1:43123"

        def __init__(self, code):
            self.failure_code = code

        def start(self):
            return self

        def stop(self):
            return None

    def runner(arguments, _prompt, _cwd, _environment):
        return subprocess.CompletedProcess(arguments, 1, "", "")

    for code, expected in (
        ("broker_dns_failed", assistance.AssistanceTransientError),
        ("broker_host_blocked", AssistanceConfigurationError),
    ):
        checked = CodexInvoker(
            codex_binary=codex,
            auth_file=auth,
            runner=runner,
            identity_verifier=lambda _path, value=initial: value,
            broker_factory=lambda value=code: Broker(value),  # type: ignore[arg-type]
        )
        with pytest.raises(expected, match=code):
            checked.invoke({"operation": "triage", "claims": []})

    class StartFailure(Broker):
        def start(self):
            raise BrokerError("broker_start_failed", "no bind")

    failed = CodexInvoker(
        codex_binary=codex,
        auth_file=auth,
        runner=runner,
        identity_verifier=lambda _path: initial,
        broker_factory=lambda: StartFailure(""),  # type: ignore[arg-type]
    )
    with pytest.raises(AssistanceConfigurationError, match="start_failed"):
        failed.invoke({"operation": "triage", "claims": []})

    for output, condition in (
        ("You've hit your usage limit. Check the rate limit reset time.", "waiting_for_usage_reset"),
        ("Your access token expired. Please log in again.", "waiting_for_login"),
    ):
        deferred = CodexInvoker(
            codex_binary=codex,
            auth_file=auth,
            runner=lambda arguments, *_args, message=output: subprocess.CompletedProcess(
                arguments, 1, "", message
            ),
            identity_verifier=lambda _path: initial,
            broker_factory=lambda: Broker(""),  # type: ignore[arg-type]
        )
        with pytest.raises(assistance.AssistanceDeferred, match=condition):
            deferred.invoke({"operation": "triage", "claims": []})
