"""Sign in with ChatGPT for this locally hosted open-source application.

Uses dynamic public-client registration, PKCE, verified OpenID tokens and
serialized token rotation. Credentials never leave local protected storage.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid
import webbrowser

import httpx

AUTH_ORIGIN = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
_THREAD_LOCK = threading.RLock()
_OAUTH_ERROR_CATEGORIES = frozenset({"invalid_request", "invalid_client", "invalid_grant", "unauthorized_client",
    "unsupported_grant_type", "invalid_scope", "access_denied", "server_error", "temporarily_unavailable",
    "invalid_target", "consent_required", "login_required", "interaction_required", "account_selection_required"})


def _oauth_error_category(value):
    """Expose only protocol categories, never arbitrary endpoint response text."""
    if isinstance(value, dict):
        value = value.get("code")
    return value if isinstance(value, str) and value in _OAUTH_ERROR_CATEGORIES else ""


class ChatGPTPlanError(RuntimeError):
    pass


class ChatGPTPlanAuth:
    def __init__(self, config_root: Path, *, client=None, clock=time.time):
        self.root = Path(config_root)
        self.path = self.root / "chatgpt-plan.json"
        self.client = client or httpx.Client(timeout=20, follow_redirects=False)
        self.clock = clock

    def _read(self):
        try:
            if self.path.is_symlink():
                raise ChatGPTPlanError("ChatGPT credential storage must be a regular protected file.")
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}
        except (ValueError, OSError) as error:
            raise ChatGPTPlanError("Cannot read the protected ChatGPT registration. Sign in again locally.") from error

    def _write(self, data):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=self.root, prefix=".chatgpt-plan-", delete=False) as file:
            temporary = Path(file.name)
            os.chmod(temporary, 0o600)
            json.dump(data, file)
            file.flush(); os.fsync(file.fileno())
        try:
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def _locked(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with _THREAD_LOCK, open(self.root / ".chatgpt-plan.lock", "a+") as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def status(self):
        try:
            record = self._read()
        except ChatGPTPlanError as error:
            return {"ready": False, "message": str(error)}
        ready = bool(record.get("access_token") and {"chatgpt.tokens.use.direct", "resource.invoke"}.issubset(record.get("scopes", [])))
        return {"ready": ready, "email": record.get("email", "") if ready else "",
                "message": "Connected to your ChatGPT plan for generation on this Mac." if ready else
                "Continue with ChatGPT locally: open-source-ai-news-wire generation login. No paid API fallback is enabled.",
                "usage_url": "https://chatgpt.com/settings/usage"}

    def _post_tokens(self, data):
        response = None
        try:
            response = self.client.post(AUTH_ORIGIN + "/api/accounts/oauth/token", data=data)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            category = ""
            if response is not None:
                try:
                    payload = response.json()
                    category = _oauth_error_category(payload.get("error")) if isinstance(payload, dict) else ""
                except ValueError:
                    pass
            detail = f" ({category})" if category else ""
            raise ChatGPTPlanError(f"ChatGPT authorization could not complete{detail}. Start a fresh local sign-in in the Mac Terminal.") from error
        if not isinstance(payload, dict) or not payload.get("access_token") or str(payload.get("token_type", "")).lower() != "bearer":
            raise ChatGPTPlanError("ChatGPT returned an incomplete token response.")
        return payload

    def _verify_identity(self, token, client_id, nonce=None):
        try:
            import jwt
        except ImportError as error:
            raise ChatGPTPlanError("Install the application's PyJWT crypto dependency before signing in.") from error
        try:
            discovery = self.client.get(AUTH_ORIGIN + "/.well-known/openid-configuration")
            discovery.raise_for_status(); metadata = discovery.json()
            uri = metadata["jwks_uri"]
            if urlsplit(uri).scheme != "https" or urlsplit(uri).hostname != "auth.openai.com":
                raise ValueError("untrusted JWKS origin")
            keys = self.client.get(uri); keys.raise_for_status()
            header = jwt.get_unverified_header(token)
            key_data = next(k for k in keys.json()["keys"] if k.get("kid") == header.get("kid"))
            key = jwt.PyJWK.from_dict(key_data)
            claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=client_id,
                                issuer=AUTH_ORIGIN, options={"require": ["exp", "iat", "sub", "iss", "aud"]})
            if nonce is not None and not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
                raise ValueError("nonce mismatch")
            return claims
        except Exception as error:
            raise ChatGPTPlanError("ChatGPT identity validation failed. Start a fresh local sign-in.") from error

    def login(self, *, open_browser=webbrowser.open, timeout=180):
        # An attempt stays separate from saved credentials until all validation passes.
        with self._locked():
            previous = self._read()
            host_id = previous.get("ext_agent_host_id") or "urn:uuid:" + str(uuid.uuid4())
            if not previous.get("ext_agent_host_id"):
                previous["ext_agent_host_id"] = host_id
                self._write(previous)
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        callback = {}
        expected_client = previous.get("client_id") or "dynamic_agent_client"

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                parsed = urlsplit(self.path)
                values = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                valid = parsed.path == "/auth/callback" and secrets.compare_digest(values.get("state", ""), state)
                if valid:
                    callback.update(values)
                self.send_response(200 if valid else 400); self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers(); self.wfile.write(
                    b"Callback received. Return to the Mac Terminal where you ran generation login. "
                    b"Connection is complete only when that command reports ready: true. "
                    b"Signing into the News Wire website does not connect generation on this Mac."
                    if valid else b"Invalid sign-in callback. Return to the initiating Mac Terminal for the connection status.")

        try:
            server = HTTPServer(("127.0.0.1", 0), Handler)
        except OSError as error:
            raise ChatGPTPlanError("The local sign-in callback could not start. Run generation login in your Mac's Terminal.") from error
        server.timeout = .5
        redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
        parameters = dict(client_id=expected_client, ext_agent_host_id=host_id, response_type="code",
                          redirect_uri=redirect, scope=SCOPES, resource=RESOURCE, state=state, nonce=nonce,
                          code_challenge_method="S256", code_challenge=challenge)
        if expected_client == "dynamic_agent_client":
            parameters["agent_name_hint"] = "newswire"
        elif previous.get("id_token"):
            parameters["id_token_hint"] = previous["id_token"]
        try:
            open_browser(AUTH_ORIGIN + "/api/accounts/authorize?" + urlencode(parameters))
            deadline = time.monotonic() + timeout
            while not callback and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()
        if not callback:
            raise ChatGPTPlanError("ChatGPT sign-in timed out. Run generation login again locally.")
        if callback.get("error"):
            category = _oauth_error_category(callback["error"])
            detail = f" ({category})" if category else ""
            raise ChatGPTPlanError(f"ChatGPT sign-in did not complete{detail}. No credentials were replaced.")
        issued = callback.get("client_id", expected_client)
        if issued == "dynamic_agent_client" or (expected_client != "dynamic_agent_client" and issued != expected_client):
            raise ChatGPTPlanError("ChatGPT returned an unexpected client registration.")
        payload = self._post_tokens(dict(grant_type="authorization_code", client_id=issued,
                                        code=callback.get("code", ""), code_verifier=verifier,
                                        redirect_uri=redirect, resource=RESOURCE))
        identity = self._verify_identity(payload.get("id_token", ""), issued, nonce)
        if previous.get("subject") and identity["sub"] != previous["subject"]:
            raise ChatGPTPlanError("The selected ChatGPT account changed. Sign out before adding a different account.")
        scopes = payload.get("scope", "").split()
        if "chatgpt.tokens.use.direct" not in scopes or "resource.invoke" not in scopes:
            raise ChatGPTPlanError("ChatGPT plan usage was not authorized. Enable plan usage during local sign-in.")
        record = dict(payload, client_id=issued, ext_agent_host_id=host_id, scopes=scopes,
                      subject=identity["sub"], email=identity.get("email", ""), issuer=identity["iss"],
                      expires_at=self.clock() + int(payload.get("expires_in", 3600)))
        with self._locked():
            self._write(record)
        return self.status()

    def access_token(self):
        with self._locked():
            record = self._read()
            if not record.get("access_token") or not {"chatgpt.tokens.use.direct", "resource.invoke"}.issubset(record.get("scopes", [])):
                raise ChatGPTPlanError("Continue with ChatGPT using the local generation login command first.")
            if float(record.get("expires_at", 0)) < self.clock() + 90:
                if not record.get("refresh_token"):
                    raise ChatGPTPlanError("ChatGPT authorization expired. Sign in again locally.")
                payload = self._post_tokens(dict(grant_type="refresh_token", client_id=record["client_id"],
                                                refresh_token=record["refresh_token"], resource=RESOURCE))
                scopes = payload.get("scope", " ".join(record["scopes"])).split()
                if not {"chatgpt.tokens.use.direct", "resource.invoke"}.issubset(scopes):
                    raise ChatGPTPlanError("ChatGPT plan permission was revoked. Sign in again locally.")
                if payload.get("id_token"):
                    identity = self._verify_identity(payload["id_token"], record["client_id"])
                    if identity["sub"] != record["subject"]:
                        raise ChatGPTPlanError("ChatGPT refresh returned a different account.")
                record.update(payload, scopes=scopes, expires_at=self.clock() + int(payload.get("expires_in", 3600)))
                self._write(record)
            return record["access_token"]

    def logout(self):
        confirmed = True
        with self._locked():
            record = self._read()
            if record.get("refresh_token"):
                try:
                    discovery = self.client.get(AUTH_ORIGIN + "/.well-known/openid-configuration")
                    discovery.raise_for_status(); endpoint = discovery.json()["revocation_endpoint"]
                    if urlsplit(endpoint).hostname != "auth.openai.com" or urlsplit(endpoint).scheme != "https":
                        raise ValueError("invalid revocation endpoint")
                    response = self.client.post(endpoint, data=dict(token=record["refresh_token"],
                                                token_type_hint="refresh_token", client_id=record["client_id"]))
                    response.raise_for_status()
                except (httpx.HTTPError, ValueError, KeyError):
                    confirmed = False
            retained = {k: v for k, v in record.items() if k in {"client_id", "ext_agent_host_id", "subject", "email", "issuer"}}
            self._write(retained)
        return {"ready": False, "message": "Signed out." if confirmed else
                "Signed out locally. Remote revocation was not confirmed; disconnect News Wire in ChatGPT Settings.",
                "revocation_confirmed": confirmed}
