from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback

import httpx
import pytest

from open_source_ai_news_wire.telegram import TelegramAPIError, TelegramClient, TelegramError


TOKEN = "12345:dummy-test-token"


def test_transport_is_fixed_host_and_disables_paid_broadcast(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    seen = []

    async def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    client = TelegramClient(TOKEN, transport=httpx.MockTransport(respond))
    result = client.call("sendMessage", {
        "chat_id": -1001, "text": "Test article", "allow_paid_broadcast": True,
    })
    assert result["message_id"] == 7
    assert str(seen[0].url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    body = json.loads(seen[0].content)
    assert body["allow_paid_broadcast"] is False
    assert body["link_preview_options"] == {"is_disabled": True}


def test_get_updates_preserves_list_and_redirects_are_not_followed() -> None:
    calls = []

    async def respond(request):
        calls.append(request)
        if request.url.path.endswith("getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [{"update_id": 3}]})
        return httpx.Response(302, headers={"location": "https://other.example/"})

    client = TelegramClient(TOKEN, transport=httpx.MockTransport(respond))
    assert client.call("getUpdates", {}) == [{"update_id": 3}]
    with pytest.raises(TelegramAPIError):
        client.call("getMe", {})
    assert len(calls) == 2


def test_transport_total_timeout_and_traceback_redaction() -> None:
    async def respond(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json={"ok": True, "result": {}})

    started = time.monotonic()
    with pytest.raises(TelegramAPIError) as caught:
        TelegramClient(TOKEN, transport=httpx.MockTransport(respond)).call("getMe", {}, timeout=0.1)
    assert time.monotonic() - started < 1
    assert TOKEN not in "".join(traceback.format_exception(caught.value))


def test_network_exception_and_child_logger_cannot_expose_token(caplog) -> None:
    async def respond(request):
        logging.getLogger("httpcore.http11").debug("request %s", request.url)
        raise httpx.ConnectError(f"Cannot connect to {request.url}", request=request)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(TelegramAPIError) as caught:
            TelegramClient(TOKEN, transport=httpx.MockTransport(respond)).call("getMe", {})
    assert TOKEN not in caplog.text
    assert TOKEN not in "".join(traceback.format_exception(caught.value))


def test_response_size_is_bounded_before_all_chunks_are_read() -> None:
    chunks_read = []

    class LargeBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            for number in range(100):
                chunks_read.append(number)
                yield b"x" * 65536

    async def respond(request):
        return httpx.Response(200, stream=LargeBody())

    with pytest.raises(TelegramAPIError, match="response_too_large"):
        TelegramClient(TOKEN, transport=httpx.MockTransport(respond)).call("getMe", {})
    assert len(chunks_read) < 20


@pytest.mark.parametrize("result", [{}, {"message_id": None}, {"message_id": True}, {"message_id": -1}])
def test_send_requires_positive_provider_acknowledgement(result) -> None:
    async def respond(request):
        return httpx.Response(200, json={"ok": True, "result": result})

    with pytest.raises(TelegramAPIError):
        TelegramClient(TOKEN, transport=httpx.MockTransport(respond)).call(
            "sendMessage", {"chat_id": -1001, "text": "Test"}
        )


@pytest.mark.parametrize("token", ["", "123:abc/evil", "123:abc?x=1", "123:abc#fragment"])
def test_invalid_tokens_cannot_change_request_path(token) -> None:
    with pytest.raises(TelegramError):
        TelegramClient(token)


def test_rate_limit_without_retry_delay_still_pauses_channel() -> None:
    async def respond(request):
        return httpx.Response(429, json={"ok": False, "error_code": 429})

    with pytest.raises(TelegramAPIError) as caught:
        TelegramClient(TOKEN, transport=httpx.MockTransport(respond)).call("getMe", {})
    assert caught.value.retry_after is not None and caught.value.retry_after > 0
    assert not caught.value.permanent
