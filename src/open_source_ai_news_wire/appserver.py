"""Small, injectable stdio client for the versioned Codex app-server protocol.

Only the backend sees the process and its credentials. No TCP listener is opened.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from typing import Any


class AppServerError(RuntimeError):
    pass


def codex_executable() -> str:
    configured = os.environ.get("NEWSWIRE_CODEX_BINARY")
    candidates = [configured, shutil.which("codex")]
    for root in (Path("/Applications"), Path.home() / "Applications"):
        candidates.extend(str(root / suffix) for suffix in (
            "ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
            "ChatGPT.app/Contents/Resources/codex",
            "Codex.app/Contents/Resources/codex",
        ))
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise AppServerError("Codex is unavailable. Install the desktop app or configure NEWSWIRE_CODEX_BINARY.")


class StdioAppServer:
    def __init__(self, *, access_token: str | None = None, state_root: Path | None = None,
                 process_factory=subprocess.Popen, timeout: float = 30):
        args = [codex_executable(), "app-server", "--listen", "stdio://",
                "-c", "analytics.enabled=false", "-c", "feedback.enabled=false"]
        environment = os.environ.copy()
        # Never route this application's requests through an inherited paid key.
        environment.pop("OPENAI_API_KEY", None)
        if state_root:
            state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            args += ["-c", "sqlite_home=" + json.dumps(str(state_root)),
                     "-c", "log_dir=" + json.dumps(str(state_root)),
                     "-c", 'history.persistence="none"']
        if access_token:
            environment["NEWSWIRE_CHATGPT_ACCESS_TOKEN"] = access_token
            args += ["-c", 'model_provider="newswire_chatgpt_plan"',
                     "-c", 'model_providers.newswire_chatgpt_plan.name="ChatGPT plan"',
                     "-c", 'model_providers.newswire_chatgpt_plan.base_url="https://api.openai.com/v1"',
                     "-c", 'model_providers.newswire_chatgpt_plan.env_key="NEWSWIRE_CHATGPT_ACCESS_TOKEN"',
                     "-c", 'model_providers.newswire_chatgpt_plan.wire_api="responses"',
                     "-c", 'model_providers.newswire_chatgpt_plan.requires_openai_auth=false',
                     "-c", 'model_providers.newswire_chatgpt_plan.supports_websockets=false',
                     "-c", 'shell_environment_policy.inherit="core"',
                     "-c", 'shell_environment_policy.exclude=["NEWSWIRE_CHATGPT_ACCESS_TOKEN","OPENAI_API_KEY","*TOKEN*","*KEY*","*SECRET*"]',
                     "--disable", "apps", "--disable", "image_generation",
                     "--disable", "code_mode_tool_search"]
        dependency_root = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies'
        if dependency_root.is_dir():
            environment['PATH'] = str(dependency_root / 'node/bin') + ':' + str(dependency_root / 'python/bin') + ':' + environment.get('PATH', '/usr/bin:/bin')
        self.process = process_factory(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, text=True, bufsize=1, env=environment)
        self.timeout = timeout
        self._events: queue.Queue = queue.Queue()
        self._deferred: list[dict] = []
        self._sequence = 0
        self._write_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self.closed = False
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.request("initialize", {"clientInfo": {"name": "newswire", "title": "News Wire", "version": "0.6.0"},
                                        "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                if len(line) > 8_000_000:
                    self._events.put(AppServerError("Codex returned an oversized event.")); break
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(event, dict):
                    self._events.put(event)
        finally:
            self._events.put(AppServerError("Codex stopped unexpectedly. Check the local runtime and reconnect."))

    def send(self, event: dict):
        if self.closed:
            raise AppServerError("Codex session has closed.")
        try:
            with self._write_lock:
                self.process.stdin.write(json.dumps(event) + "\n")
                self.process.stdin.flush()
        except (OSError, ValueError) as error:
            raise AppServerError("Codex session disconnected.") from error

    def request(self, method: str, params: dict | None = None) -> Any:
        with self._request_lock:
            self._sequence += 1
            request_id = self._sequence
            self.send({"id": request_id, "method": method, "params": params or {}})
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                try:
                    item = self._events.get(timeout=max(.01, deadline - time.monotonic()))
                except queue.Empty:
                    break
                if isinstance(item, Exception):
                    raise item
                if item.get("id") == request_id and "method" not in item:
                    if "error" in item:
                        # Do not relay process error text, which can contain local paths or secrets.
                        raise AppServerError(f"Codex rejected {method} (code {item['error'].get('code', 'unknown')}).")
                    return item.get("result", {})
                self._deferred.append(item)
            raise AppServerError(f"Codex {method} timed out.")

    def next_event(self, timeout: float = 1) -> dict | None:
        if self._deferred:
            return self._deferred.pop(0)
        try:
            event = self._events.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(event, Exception):
            raise event
        return event

    def respond(self, request_id: Any, result: dict):
        self.send({"id": request_id, "result": result})

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait(timeout=3)
