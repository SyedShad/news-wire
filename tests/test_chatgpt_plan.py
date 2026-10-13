import json
from pathlib import Path
import time
from urllib.parse import parse_qs, urlsplit
import io

import httpx
import pytest

from open_source_ai_news_wire.chatgpt_plan import ChatGPTPlanAuth, ChatGPTPlanError


def record(auth, **updates):
    payload = {"client_id": "test-issued-client", "subject": "test-subject", "email": "test@example.invalid",
               "ext_agent_host_id": "urn:uuid:test-host", "access_token": "obvious-test-access",
               "refresh_token": "obvious-test-refresh", "scopes": ["chatgpt.tokens.use.direct", "resource.invoke"],
               "expires_at": time.time() + 3600}
    payload.update(updates); auth._write(payload); return payload


def test_status_never_exposes_tokens_and_requires_plan_permission(tmp_path):
    auth = ChatGPTPlanAuth(tmp_path)
    assert not auth.status()["ready"]
    record(auth)
    assert auth.status()["ready"]
    assert "generation on this Mac" in auth.status()["message"]
    assert "obvious-test" not in json.dumps(auth.status())
    assert auth.path.stat().st_mode & 0o777 == 0o600
    record(auth, scopes=["openid"])
    with pytest.raises(ChatGPTPlanError, match="login"):
        auth.access_token()


def test_refresh_rotates_credentials_without_secret_output(tmp_path):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"access_token": "obvious-replacement-access", "refresh_token": "obvious-replacement-refresh",
                                        "token_type": "Bearer", "expires_in": 3600})
    auth = ChatGPTPlanAuth(tmp_path, client=httpx.Client(transport=httpx.MockTransport(handle)))
    record(auth, expires_at=0)
    assert auth.access_token() == "obvious-replacement-access"
    assert json.loads(auth.path.read_text())["refresh_token"] == "obvious-replacement-refresh"
    assert len(requests) == 1
    assert b"client_id=test-issued-client" in requests[0].content
    assert b"grant_type=refresh_token" in requests[0].content
    assert auth.access_token() == "obvious-replacement-access"
    assert len(requests) == 1


def test_refresh_error_is_safe_and_leaves_existing_record(tmp_path):
    auth = ChatGPTPlanAuth(tmp_path, client=httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(400, json={"error": "invalid_grant", "secret": "never-display-this"}))))
    saved = record(auth, expires_at=0)
    with pytest.raises(ChatGPTPlanError) as error:
        auth.access_token()
    assert "never-display" not in str(error.value)
    assert json.loads(auth.path.read_text()) == saved


def test_logout_clears_tokens_and_reports_unconfirmed_revocation(tmp_path):
    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json={"revocation_endpoint": "https://auth.openai.com/revoke"})
        return httpx.Response(503)
    auth = ChatGPTPlanAuth(tmp_path, client=httpx.Client(transport=httpx.MockTransport(handle)))
    record(auth)
    result = auth.logout()
    assert result["revocation_confirmed"] is False
    assert not auth.status()["ready"]
    stored = json.loads(auth.path.read_text())
    assert stored["client_id"] == "test-issued-client"
    assert not any(key.endswith("token") for key in stored)


def test_jwks_identity_verification_rejects_nonce_audience_and_signature(tmp_path):
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public['kid'] = 'test-key'
    def handle(request):
        return httpx.Response(200, json={"jwks_uri": "https://auth.openai.com/jwks"} if 'configuration' in str(request.url) else {"keys": [public]})
    auth = ChatGPTPlanAuth(tmp_path, client=httpx.Client(transport=httpx.MockTransport(handle)))
    claims = dict(iss="https://auth.openai.com", aud="test-issued-client", exp=time.time()+3600, iat=time.time(), sub="account-test", nonce="correct-nonce")
    token = jwt.encode(claims, key, algorithm='RS256', headers={'kid': 'test-key'})
    assert auth._verify_identity(token, 'test-issued-client', 'correct-nonce')['sub'] == 'account-test'
    with pytest.raises(ChatGPTPlanError):
        auth._verify_identity(token, 'test-issued-client', 'wrong-nonce')
    with pytest.raises(ChatGPTPlanError):
        auth._verify_identity(token, 'wrong-client', 'correct-nonce')
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(claims, wrong_key, algorithm='RS256', headers={'kid': 'test-key'})
    with pytest.raises(ChatGPTPlanError):
        auth._verify_identity(forged, 'test-issued-client', 'correct-nonce')


def test_login_pkce_callback_issued_client_and_failed_identity_preserve_tokens(tmp_path, monkeypatch):
    posts = []
    def handle(request):
        posts.append(request)
        return httpx.Response(200, json={"access_token": "obvious-new-access", "id_token": "fake-id-token",
               "refresh_token": "obvious-new-refresh", "token_type": "Bearer", "expires_in": 3600,
               "scope": "openid resource.invoke chatgpt.tokens.use.direct"})
    auth = ChatGPTPlanAuth(tmp_path, client=httpx.Client(transport=httpx.MockTransport(handle)))
    captured = {}
    class CallbackServer:
        server_port = 1455
        def __init__(self, _address, handler):
            self.handler = handler
        def handle_request(self):
            request = self.handler.__new__(self.handler)
            request.path = '/auth/callback?code=test-code&state='+captured['state']+'&client_id=test-dynamic-issued'
            request.send_response = lambda *args: None
            request.send_header = lambda *args: None
            request.end_headers = lambda: None
            request.wfile = io.BytesIO()
            request.do_GET()
            captured['callback_message'] = request.wfile.getvalue().decode()
        def server_close(self):
            pass
    monkeypatch.setattr('open_source_ai_news_wire.chatgpt_plan.HTTPServer', CallbackServer)
    def opener(url):
        params = {k:v[0] for k,v in parse_qs(urlsplit(url).query).items()}; captured.update(params)
    def invalid_identity(*args):
        raise ChatGPTPlanError('Identity rejected')
    auth._verify_identity = invalid_identity
    with pytest.raises(ChatGPTPlanError, match='Identity rejected'):
        auth.login(open_browser=opener, timeout=3)
    assert captured['client_id'] == 'dynamic_agent_client'
    assert captured['code_challenge_method'] == 'S256'
    assert captured['resource'] == 'https://api.openai.com/v1'
    assert 'Mac Terminal where you ran generation login' in captured['callback_message']
    assert 'only when that command reports ready: true' in captured['callback_message']
    assert 'website does not connect generation' in captured['callback_message']
    assert b'client_id=test-dynamic-issued' in posts[0].content
    assert not json.loads(auth.path.read_text()).get('access_token')


def test_cli_auth_lifecycle_does_not_require_database_migration(tmp_path, monkeypatch, capsys):
    from open_source_ai_news_wire import cli
    def database_must_not_be_opened(*args):
        raise AssertionError('Auth commands must not initialize an existing database')
    monkeypatch.setattr(cli, '_database', database_must_not_be_opened)
    assert cli.main(['--data-root',str(tmp_path),'generation','status']) == 0
    assert 'Continue with ChatGPT' in capsys.readouterr().out
    monkeypatch.setattr(ChatGPTPlanAuth, 'login', lambda self: {'ready':True,'message':'Connected'})
    assert cli.main(['--data-root',str(tmp_path),'generation','login']) == 0
    assert 'Connected' in capsys.readouterr().out


@pytest.mark.parametrize("remote_error, expected_category", [
    ("invalid_grant", "invalid_grant"),
    ({"code": "invalid_scope", "message": "private-secret"}, "invalid_scope"),
    ("private-secret", ""),
])
def test_token_error_reports_only_safe_oauth_category(tmp_path, remote_error, expected_category):
    auth = ChatGPTPlanAuth(tmp_path, client=httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(400, json={"error": remote_error, "error_description": "private-secret"}))))
    with pytest.raises(ChatGPTPlanError) as raised:
        auth._post_tokens({"grant_type": "authorization_code", "code": "private-code"})
    message = str(raised.value)
    assert "private-secret" not in message and "private-code" not in message
    assert "Mac Terminal" in message
    if expected_category:
        assert "(" + expected_category + ")" in message
    else:
        assert "authorization could not complete." in message
