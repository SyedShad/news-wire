from __future__ import annotations

import builtins
import ctypes
import json
import plistlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from open_source_ai_news_wire import cli
from open_source_ai_news_wire.telegram_keychain import (
    TELEGRAM_KEYCHAIN_SERVICE,
    TelegramKeychain,
    _SecurityBackend,
)
from open_source_ai_news_wire.telegram_scheduler import TelegramLaunchAgent, TelegramSetupLock


class FakeKeychain:
    def __init__(self):
        self.values: dict[tuple[str, str], str] = {}
        self.account = "runtime-test"

    def get_token(self):
        return self.values.get((TELEGRAM_KEYCHAIN_SERVICE, self.account))

    def store_token(self, token: str):
        self.values[(TELEGRAM_KEYCHAIN_SERVICE, self.account)] = token

    def delete_token(self):
        self.values.pop((TELEGRAM_KEYCHAIN_SERVICE, self.account), None)


class FakeBackend:
    def __init__(self):
        self.values: dict[tuple[str, str], str] = {}

    def get(self, service: str, account: str):
        return self.values.get((service, account))

    def put(self, service: str, account: str, value: str):
        self.values[(service, account)] = value

    def delete(self, service: str, account: str):
        self.values.pop((service, account), None)


def test_keychain_fake_round_trip_uses_runtime_scoped_account() -> None:
    backend = FakeBackend()
    keychain = TelegramKeychain("/tmp/runtime-a", backend=backend)

    keychain.store_token("12345:dummy-token")
    assert keychain.get_token() == "12345:dummy-token"
    assert keychain.account != str(Path("/tmp/runtime-a"))
    keychain.delete_token()
    assert keychain.get_token() is None


@pytest.mark.skipif(sys.platform != "darwin", reason="Security.framework is macOS-only")
def test_native_keychain_dictionary_path_allocates_and_releases_without_reading_items() -> None:
    backend = _SecurityBackend()
    dictionary, refs = backend._base("com.example.test", "dummy-account")
    assert dictionary.value
    assert len(refs) == 2
    backend._release(dictionary, refs)


@pytest.mark.skipif(sys.platform != "darwin", reason="Security.framework is macOS-only")
def test_native_keychain_data_path_allocates_and_releases_in_memory_only() -> None:
    backend = _SecurityBackend()
    data, buffer = backend._data("dummy-token")
    assert data.value
    assert buffer.value == b"dummy-token"
    assert backend.core_foundation.CFDataGetLength(data) == len(buffer.value)
    backend.core_foundation.CFRelease(data)


def test_security_backend_fake_secitem_crud_status_paths() -> None:
    """Exercise SecItem update/add/read/delete branches without touching a real Keychain."""

    class FakeCoreFoundation:
        def __init__(self):
            self.data: dict[int, ctypes.Array] = {}
            self.current_data_ref = None

        def CFDictionarySetValue(self, _dictionary, key, value):
            if key.value == 5:
                self.current_data_ref = value

        def CFRelease(self, _ref):
            return None

        def CFDataGetLength(self, ref):
            return len(self.data[ref.value].value)

        def CFDataGetBytePtr(self, ref):
            return ctypes.cast(self.data[ref.value], ctypes.POINTER(ctypes.c_ubyte))

    class FakeSecurity:
        def __init__(self, core):
            self.core = core
            self.value = None

        def SecItemUpdate(self, _query, _attributes):
            if self.value is None:
                return -25300
            self.value = self.core.data[self.core.current_data_ref.value].value
            return 0

        def SecItemAdd(self, _query, _result):
            if self.value is not None:
                return -25299
            self.value = self.core.data[self.core.current_data_ref.value].value
            return 0

        def SecItemCopyMatching(self, _query, result):
            if self.value is None:
                return -25300
            data_ref = ctypes.c_void_p(900)
            self.core.data[data_ref.value] = ctypes.create_string_buffer(self.value)
            result._obj.value = data_ref.value
            return 0

        def SecItemDelete(self, _query):
            if self.value is None:
                return -25300
            self.value = None
            return 0

    backend = object.__new__(_SecurityBackend)
    core = FakeCoreFoundation()
    security = FakeSecurity(core)
    backend.core_foundation = core
    backend.security = security
    backend.class_key = ctypes.c_void_p(1)
    backend.class_generic_password = ctypes.c_void_p(2)
    backend.attr_service = ctypes.c_void_p(3)
    backend.attr_account = ctypes.c_void_p(4)
    backend.value_data = ctypes.c_void_p(5)
    backend.return_data = ctypes.c_void_p(6)
    backend.match_limit = ctypes.c_void_p(7)
    backend.match_limit_one = ctypes.c_void_p(8)
    backend.use_authentication_ui = ctypes.c_void_p(9)
    backend.use_authentication_ui_fail = ctypes.c_void_p(10)
    backend.boolean_true = ctypes.c_void_p(11)
    backend._base = lambda _service, _account: (ctypes.c_void_p(100), [])
    def dictionary(values, owned=None):
        for key, value in values:
            core.CFDictionarySetValue(ctypes.c_void_p(200), key, value)
        return ctypes.c_void_p(200), list(owned or [])

    backend._dictionary = dictionary
    backend._release = lambda _dictionary, _refs: None

    next_data_ref = 300

    def data(value):
        nonlocal next_data_ref
        buffer = ctypes.create_string_buffer(value.encode("utf-8"))
        reference = ctypes.c_void_p(next_data_ref)
        next_data_ref += 1
        core.data[reference.value] = buffer
        return reference, buffer

    backend._data = data
    assert backend.get("service", "account") is None
    backend.put("service", "account", "first")  # update-not-found then add
    assert backend.get("service", "account") == "first"
    backend.put("service", "account", "second")  # update-existing
    assert backend.get("service", "account") == "second"
    backend.delete("service", "account")
    assert backend.get("service", "account") is None
    backend.delete("service", "account")  # delete-not-found is idempotent


def test_scheduler_plist_keeps_stable_launcher_symlink(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    logs = root / "operations" / "logs"
    logs.mkdir(parents=True)
    database = SimpleNamespace(paths=SimpleNamespace(root=root, operations=root / "operations"))
    target = tmp_path / "release" / "bin" / "open-source-ai-news-wire"
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    stable = tmp_path / "stable" / "open-source-ai-news-wire"
    stable.parent.mkdir()
    stable.symlink_to(target)
    calls: list[list[str]] = []

    def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    manager = TelegramLaunchAgent(database, launcher=stable, runner=runner)
    manager.plist_path = tmp_path / "telegram.plist"
    manager.install()
    payload = plistlib.loads(manager.plist_path.read_bytes())
    assert payload["ProgramArguments"][0] == str(stable)
    assert payload["StartInterval"] == 60
    assert payload["RunAtLoad"] is True
    assert payload["ProgramArguments"][-1] == "50"
    assert all("token" not in json.dumps(payload).lower() for _ in [0])


def test_scheduler_disable_unloads_but_preserves_plist(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    (root / "operations" / "logs").mkdir(parents=True)
    database = SimpleNamespace(paths=SimpleNamespace(root=root, operations=root / "operations"))
    calls: list[list[str]] = []

    def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    manager = TelegramLaunchAgent(database, launcher=tmp_path / "launcher", runner=runner)
    manager.plist_path = tmp_path / "telegram.plist"
    manager.plist_path.write_bytes(b"fixture")
    manager.disable()
    assert manager.plist_path.exists()
    assert any(arguments[:2] == ["bootout", manager.domain] for arguments in calls)


def test_scheduler_enable_rewrites_stale_runtime_plist(tmp_path: Path) -> None:
    root = tmp_path / "runtime-current"
    (root / "operations" / "logs").mkdir(parents=True)
    database = SimpleNamespace(paths=SimpleNamespace(root=root, operations=root / "operations"))
    launcher = tmp_path / "launcher"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    calls: list[list[str]] = []

    def runner(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    manager = TelegramLaunchAgent(database, launcher=launcher, runner=runner)
    manager.plist_path = tmp_path / "telegram.plist"
    manager.plist_path.write_bytes(
        plistlib.dumps({"Label": "com.opensourceainewswire.telegram", "ProgramArguments": ["old-runtime"]})
    )
    manager.enable()
    payload = plistlib.loads(manager.plist_path.read_bytes())
    arguments = payload["ProgramArguments"]
    assert arguments[2] == str(root)
    assert arguments[0] == str(launcher)
    assert any(arguments[:2] == ["bootstrap", manager.domain] for arguments in calls)


def test_setup_lock_serializes_runtime_lifecycle(tmp_path: Path) -> None:
    path = tmp_path / "operations" / "locks" / "telegram-setup.lock"
    first = TelegramSetupLock(path)
    second = TelegramSetupLock(path)
    assert first.acquire()
    try:
        assert not second.acquire(blocking=False)
    finally:
        first.release()
    assert second.acquire(blocking=False)
    second.release()


def test_cli_pair_uses_hidden_token_and_never_sends_message(monkeypatch, tmp_path: Path, capsys) -> None:
    class Client:
        calls: list[tuple[str, dict[str, object]]] = []

        def __init__(self, token: str):
            assert token == "12345:dummy-token"

        def call(self, method: str, payload: dict[str, object], timeout: int = 10):
            self.calls.append((method, payload))
            if method == "getMe":
                return {"id": 123, "username": "wire_bot", "can_read_all_group_messages": False}
            if method == "getWebhookInfo":
                return {"url": ""}
            if method == "getUpdates":
                return [{
                    "update_id": 10,
                    "message": {
                        "text": "/newswire_pair@wire_bot abc123",
                        "chat": {"id": -1001, "title": "Private Team", "type": "supergroup"},
                    },
                }]
            if method == "getChat":
                return {"id": -1001, "title": "Private Team", "type": "supergroup"}
            if method == "getChatMember":
                return {"status": "member"}
            raise AssertionError(method)

    class Service:
        def __init__(self, _database):
            self.paired_args = None

        def disable(self, **_kwargs):
            return None

        def pair(self, **kwargs):
            self.paired_args = kwargs

    class Scheduler:
        def __init__(self):
            self.disabled = 0

        def disable(self, **_kwargs):
            self.disabled += 1

    keychain = FakeKeychain()
    scheduler = Scheduler()
    service = Service(None)
    monkeypatch.setattr(cli, "_telegram_components", lambda *_args: (None, keychain, scheduler, Client, object, lambda _db: service))
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "12345:dummy-token")
    monkeypatch.setattr(cli.secrets, "token_urlsafe", lambda _n: "abc123")
    monkeypatch.setattr(builtins, "input", lambda _prompt: "y")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    code = cli.main(["--data-root", str(tmp_path), "telegram", "pair", "--confirm"])

    assert code == 0
    assert scheduler.disabled == 1
    assert keychain.values
    output = capsys.readouterr().out
    assert "dummy-token" not in output
    assert "paired" in output
    assert all(method != "sendMessage" for method, _payload in Client.calls)


def test_cli_enable_requires_receipt_confirmation(monkeypatch, tmp_path: Path, capsys) -> None:
    class Service:
        def __init__(self, _database):
            pass

        def status(self):
            return {"paired": True, "test_passed": True}

        def enable(self):
            return {"enabled": True}

    class Scheduler:
        def enable(self):
            return None

    monkeypatch.setattr(cli, "_telegram_components", lambda *_args: (None, FakeKeychain(), Scheduler(), object, object, Service))
    monkeypatch.setattr(builtins, "input", lambda _prompt: "n")
    code = cli.main(["--data-root", str(tmp_path), "telegram", "enable"])
    assert code == 2
    assert "confirmation_required" in capsys.readouterr().out


def test_cli_send_test_rejects_mismatched_paired_bot(monkeypatch, tmp_path: Path, capsys) -> None:
    class Client:
        calls: list[str] = []

        def __init__(self, _token: str):
            pass

        def call(self, method: str, _payload: dict[str, object], timeout: int = 10):
            self.calls.append(method)
            if method == "getMe":
                return {"id": 999, "username": "different_bot"}
            raise AssertionError(method)

    class Service:
        def __init__(self, _database):
            pass

        def status(self):
            return {"paired": True, "chat_id": -1001, "bot_id": 123}

        def mark_tested(self):
            raise AssertionError("mismatched bot must not mark the test")

    keychain = FakeKeychain()
    keychain.store_token("12345:dummy-token")
    monkeypatch.setattr(
        cli,
        "_telegram_components",
        lambda *_args: (None, keychain, SimpleNamespace(), Client, object, Service),
    )
    code = cli.main(["--data-root", str(tmp_path), "telegram", "send-test"])
    assert code == 2
    assert Client.calls == ["getMe"]
    assert "does not match" in capsys.readouterr().out


def test_cli_unpair_clears_state_when_keychain_unavailable(monkeypatch, tmp_path: Path, capsys) -> None:
    class Service:
        def __init__(self, _database):
            self.cleared = False

        def unpair(self):
            self.cleared = True
            return {"enabled": False, "paired": False}

    class Scheduler:
        def uninstall(self):
            return None

    service = Service(None)
    monkeypatch.setattr(
        cli,
        "_telegram_components",
        lambda *_args: (None, None, Scheduler(), object, object, lambda _db: service),
    )
    monkeypatch.setattr(cli, "_telegram_keychain", lambda _database: (_ for _ in ()).throw(RuntimeError("unavailable")))
    code = cli.main(["--data-root", str(tmp_path), "telegram", "unpair"])
    output = capsys.readouterr().out
    assert code == 2
    assert service.cleared
    assert '"status": "unpaired"' in output
    assert "keychain_deletion_failed" in output


def test_default_scheduler_uses_installed_launcher_not_python_module(tmp_path, monkeypatch):
    from open_source_ai_news_wire.config import resolve_runtime_paths
    from open_source_ai_news_wire.storage import Database
    monkeypatch.setattr(sys, "argv", ["/release/site-packages/open_source_ai_news_wire/__main__.py"])
    database = Database(resolve_runtime_paths(tmp_path / "wire"))
    manager = TelegramLaunchAgent(database)
    assert manager.launcher == Path.home() / ".local/bin/open-source-ai-news-wire"
