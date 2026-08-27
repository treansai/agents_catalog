from __future__ import annotations

import base64
from typing import Any, Literal

import httpx
import pytest
from pydantic import SecretStr

from ezer.config import GmailAccount, StaticTokenAuth
from ezer.connectors.auth import BearerToken
from ezer.connectors.base import (
    ConnectorConfigurationError,
    InvalidCursorError,
    TransientProviderError,
    encode_cursor,
)
from ezer.connectors.gmail import GmailConnector
from ezer.connectors.http import RetryPolicy


class FakeTokenProvider:
    async def get_token(self) -> BearerToken:
        return BearerToken("gmail-bearer")

    async def invalidate(self, token: str | None = None) -> None:
        del token


async def fail_sleep(delay: float) -> None:
    raise AssertionError(f"unexpected sleep: {delay}")


def account(
    *,
    account_id: str = "personal",
    query: Literal["in:inbox"] = "in:inbox",
) -> GmailAccount:
    return GmailAccount(
        provider="gmail",
        id=account_id,
        query=query,
        auth=StaticTokenAuth(
            type="access_token",
            access_token=SecretStr("unused-static-credential"),
        ),
    )


def encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).rstrip(b"=").decode()


def plain_message(message_id: str, text: str = "body") -> dict[str, Any]:
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "internalDate": "1777800000000",
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": f"Subject {message_id}"},
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "To", "value": "Bob <bob@example.com>"},
            ],
            "body": {"data": encoded(text), "size": len(text)},
        },
    }


async def test_bootstrap_is_race_safe_and_paginates_with_opaque_cursor() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/profile"):
            return httpx.Response(200, json={"historyId": "100"})
        if path.endswith("/messages"):
            assert request.url.params["q"] == "in:inbox"
            if request.url.params.get("pageToken") is None:
                return httpx.Response(
                    200,
                    json={
                        "messages": [{"id": "message-1"}],
                        "nextPageToken": "next-page-secret",
                    },
                )
            assert request.url.params["pageToken"] == "next-page-secret"
            return httpx.Response(200, json={"messages": [{"id": "message-2"}]})
        if path.endswith("/messages/message-1"):
            return httpx.Response(200, json=plain_message("message-1", "first"))
        if path.endswith("/messages/message-2"):
            return httpx.Response(200, json=plain_message("message-2", "second"))
        if path.endswith("/history"):
            assert request.url.params["startHistoryId"] == "100"
            assert request.url.params["labelId"] == "INBOX"
            return httpx.Response(200, json={"historyId": "101"})
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GmailConnector(
            account(),
            client,
            token_provider=FakeTokenProvider(),
            sleep=fail_sleep,
        )
        first = await connector.fetch(None, 1)
        second = await connector.fetch(first.next_cursor, 1)
        incremental = await connector.fetch(second.next_cursor, 1)

    assert [message.provider_message_id for message in first.messages] == ["message-1"]
    assert [message.provider_message_id for message in second.messages] == ["message-2"]
    assert incremental.messages == []
    assert first.next_cursor is not None
    assert "next-page-secret" not in first.next_cursor
    assert sum(request.url.path.endswith("/profile") for request in requests) == 1
    assert all(request.headers["Authorization"] == "Bearer gmail-bearer" for request in requests)


async def test_recursive_mime_prefers_nonempty_plain_then_falls_back_to_safe_html() -> None:
    requests: list[httpx.Request] = []
    payload = {
        "id": "mime-message",
        "threadId": "mime-thread",
        "internalDate": "1777800000000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "Subject", "value": "MIME message"},
                {"name": "From", "value": "Sender <sender@example.com>"},
            ],
            "body": {},
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "body": {},
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": encoded("  \r\n\t  ")},
                        },
                        {
                            "mimeType": "text/html",
                            "body": {
                                "data": encoded(
                                    "<p>Useful HTML body</p><script>stealCredentials()</script>"
                                )
                            },
                        },
                    ],
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "report.pdf",
                    "headers": [{"name": "Content-Disposition", "value": "attachment"}],
                    "body": {"attachmentId": "attachment-secret", "size": 99},
                },
                {
                    "mimeType": "image/png",
                    "headers": [{"name": "Content-ID", "value": "<inline-image>"}],
                    "body": {"data": encoded("raw-image-bytes"), "size": 15},
                },
            ],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/profile"):
            return httpx.Response(200, json={"historyId": "200"})
        if path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "mime-message"}]})
        if path.endswith("/messages/mime-message"):
            assert request.url.params["format"] == "full"
            return httpx.Response(200, json=payload)
        raise AssertionError(f"attachments must not be downloaded: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GmailConnector(
            account(),
            client,
            token_provider=FakeTokenProvider(),
            max_body_chars=200,
            sleep=fail_sleep,
        )
        batch = await connector.fetch(None, 10)

    message = batch.messages[0]
    assert message.body_text == "Useful HTML body"
    assert "stealCredentials" not in message.body_text
    assert [(item.filename, item.content_type, item.inline) for item in message.attachments] == [
        ("report.pdf", "application/pdf", False),
        ("", "image/png", True),
    ]
    assert all("attachments" not in request.url.path for request in requests)


async def test_body_is_truncated_before_entering_domain_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/profile"):
            return httpx.Response(200, json={"historyId": "300"})
        if path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "large-message"}]})
        if path.endswith("/messages/large-message"):
            return httpx.Response(200, json=plain_message("large-message", "x" * 10_000))
        raise AssertionError(str(request.url))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GmailConnector(
            account(),
            client,
            token_provider=FakeTokenProvider(),
            max_body_chars=7,
            sleep=fail_sleep,
        )
        batch = await connector.fetch(None, 10)

    assert batch.messages[0].body_text == "x" * 7


async def test_history_pages_resume_then_expired_checkpoint_resets_cursor() -> None:
    history_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/profile"):
            return httpx.Response(200, json={"historyId": "400"})
        if path.endswith("/messages"):
            return httpx.Response(200, json={})
        if path.endswith("/messages/history-message"):
            return httpx.Response(200, json=plain_message("history-message", "changed"))
        if path.endswith("/history"):
            history_requests.append(request)
            if len(history_requests) == 1:
                return httpx.Response(
                    200,
                    json={
                        "history": [{"messagesAdded": [{"message": {"id": "history-message"}}]}],
                        "historyId": "401",
                        "nextPageToken": "history-next-secret",
                    },
                )
            if len(history_requests) == 2:
                return httpx.Response(200, json={"historyId": "402"})
            return httpx.Response(404, json={"error": {"code": 404}})
        raise AssertionError(str(request.url))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GmailConnector(
            account(),
            client,
            token_provider=FakeTokenProvider(),
            sleep=fail_sleep,
        )
        bootstrap = await connector.fetch(None, 5)
        first_history = await connector.fetch(bootstrap.next_cursor, 5)
        second_history = await connector.fetch(first_history.next_cursor, 5)
        reset = await connector.fetch(second_history.next_cursor, 5)

    assert [message.provider_message_id for message in first_history.messages] == [
        "history-message"
    ]
    assert second_history.messages == []
    assert reset.cursor_reset is True
    assert reset.next_cursor is None
    assert first_history.next_cursor is not None
    assert "history-next-secret" not in first_history.next_cursor
    assert [request.url.params["startHistoryId"] for request in history_requests] == [
        "400",
        "400",
        "402",
    ]
    assert [request.url.params.get("pageToken") for request in history_requests] == [
        None,
        "history-next-secret",
        None,
    ]
    assert all(request.url.params["labelId"] == "INBOX" for request in history_requests)


async def test_cursor_is_bound_to_account_without_network_call() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del request
        calls += 1
        raise AssertionError("cursor validation must happen before HTTP")

    foreign_cursor = encode_cursor(
        account_id="another-account",
        provider="gmail",
        state={"phase": "history", "checkpoint": "1"},
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GmailConnector(
            account(),
            client,
            token_provider=FakeTokenProvider(),
            sleep=fail_sleep,
        )
        with pytest.raises(InvalidCursorError):
            await connector.fetch(foreign_cursor, 10)

    assert calls == 0


def test_custom_bootstrap_query_is_rejected_to_preserve_incremental_inbox_scope() -> None:
    unsafe_account = GmailAccount.model_construct(
        provider="gmail",
        id="personal",
        mailbox="me",
        query="from:alice@example.com",
        auth=StaticTokenAuth(
            type="access_token",
            access_token=SecretStr("unused-static-credential"),
        ),
    )
    with pytest.raises(ConnectorConfigurationError) as caught:
        GmailConnector(
            unsafe_account,
            token_provider=FakeTokenProvider(),
        )
    assert caught.value.code == "unsupported_incremental_query"


async def test_permanent_message_failures_are_bounded_dead_letters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/profile"):
            return httpx.Response(200, json={"historyId": "500"})
        if path.endswith("/messages"):
            return httpx.Response(
                200,
                json={
                    "messages": [
                        {"id": "oversized-message"},
                        {"id": "invalid-json-message"},
                        {"id": "malformed-message"},
                        {"id": "readable-message"},
                    ]
                },
            )
        if path.endswith("/messages/oversized-message"):
            return httpx.Response(
                200,
                headers={"Content-Length": str(9 * 1024 * 1024)},
                content=b"{}",
            )
        if path.endswith("/messages/invalid-json-message"):
            return httpx.Response(200, content=b"not-json")
        if path.endswith("/messages/malformed-message"):
            return httpx.Response(
                200,
                json={"id": "malformed-message", "payload": "not-a-mime-object"},
            )
        if path.endswith("/messages/readable-message"):
            return httpx.Response(200, json=plain_message("readable-message", "safe"))
        raise AssertionError(str(request.url))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GmailConnector(
            account(),
            client,
            token_provider=FakeTokenProvider(),
            sleep=fail_sleep,
        )
        batch = await connector.fetch(None, 10)

    assert [message.provider_message_id for message in batch.messages] == ["readable-message"]
    assert [
        (dead_letter.provider_message_id, dead_letter.code) for dead_letter in batch.dead_letters
    ] == [
        ("oversized-message", "response_too_large"),
        ("invalid-json-message", "invalid_json"),
        ("malformed-message", "missing_mime_payload"),
    ]
    assert batch.next_cursor is not None


async def test_transient_message_failure_remains_a_global_connector_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/profile"):
            return httpx.Response(200, json={"historyId": "600"})
        if path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "transient-message"}]})
        if path.endswith("/messages/transient-message"):
            return httpx.Response(503, json={"error": {"code": "backendError"}})
        raise AssertionError(str(request.url))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = GmailConnector(
            account(),
            client,
            token_provider=FakeTokenProvider(),
            retry_policy=RetryPolicy(max_attempts=1),
            sleep=fail_sleep,
        )
        with pytest.raises(TransientProviderError):
            await connector.fetch(None, 10)
