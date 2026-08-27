from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from ezer.config import (
    ClientCredentialsAuth,
    MsalDeviceCodeAuth,
    OutlookAccount,
    RefreshTokenAuth,
    StaticTokenAuth,
)
from ezer.connectors.auth import BearerToken
from ezer.connectors.base import (
    ConnectorConfigurationError,
    ProviderResponseError,
    ResourceNotFoundError,
    encode_cursor,
)
from ezer.connectors.outlook import OutlookConnector


class FakeTokenProvider:
    async def get_token(self) -> BearerToken:
        return BearerToken("graph-bearer")

    async def invalidate(self, token: str | None = None) -> None:
        del token


async def fail_sleep(delay: float) -> None:
    raise AssertionError(f"unexpected sleep: {delay}")


def account() -> OutlookAccount:
    return OutlookAccount(
        provider="outlook",
        id="work",
        mailbox="user@example.com",
        auth=StaticTokenAuth(
            type="access_token",
            access_token=SecretStr("unused-static-credential"),
        ),
    )


def test_personal_microsoft_account_uses_consumers_delegated_auth() -> None:
    application_account = OutlookAccount(
        provider="outlook",
        id="personal",
        mailbox="person@hotmail.fr",
        auth=ClientCredentialsAuth(
            type="client_credentials",
            tenant_id="00000000-0000-0000-0000-000000000000",
            client_id="client-id",
            client_secret=SecretStr("client-secret"),
        ),
    )
    assert isinstance(application_account.auth, MsalDeviceCodeAuth)
    OutlookConnector(application_account, token_provider=FakeTokenProvider())

    wrong_authority = OutlookAccount(
        provider="outlook",
        id="personal",
        mailbox="person@outlook.com",
        auth=RefreshTokenAuth(
            type="refresh_token",
            tenant_id="00000000-0000-0000-0000-000000000000",
            client_id="client-id",
            client_secret=SecretStr("client-secret"),
            refresh_token=SecretStr("refresh-token"),
        ),
    )
    with pytest.raises(ConnectorConfigurationError) as authority_error:
        OutlookConnector(wrong_authority, token_provider=FakeTokenProvider())
    assert authority_error.value.code == ("personal_microsoft_account_requires_consumers_authority")


async def test_personal_device_code_account_reads_graph_me_endpoint() -> None:
    personal_account = OutlookAccount(
        provider="outlook",
        id="personal",
        mailbox="person@hotmail.fr",
        auth=MsalDeviceCodeAuth(type="device_code", client_id="public-client-id"),
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/v1.0/me/mailFolders/inbox/messages/delta"
        return httpx.Response(
            200,
            json={
                "value": [],
                "@odata.deltaLink": (
                    "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta"
                    "?$deltatoken=personal"
                ),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = OutlookConnector(
            personal_account,
            client,
            token_provider=FakeTokenProvider(),
            sleep=fail_sleep,
        )
        batch = await connector.fetch(None, 10)

    assert batch.messages == []
    assert batch.next_cursor is not None
    assert len(requests) == 1


@pytest.mark.parametrize(
    "continuation",
    [
        # The form Microsoft Graph actually echoes: OData key syntax for the folder.
        "https://graph.microsoft.com/v1.0/me/mailFolders('inbox')/messages/delta"
        "?$deltatoken=personal",
        # The form this connector builds, and which older cursors may still hold.
        "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$deltatoken=personal",
    ],
)
async def test_graph_key_syntax_continuation_is_accepted_and_survives_a_cursor(
    continuation: str,
) -> None:
    personal_account = OutlookAccount(
        provider="outlook",
        id="personal",
        mailbox="person@hotmail.fr",
        auth=MsalDeviceCodeAuth(type="device_code", client_id="public-client-id"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"value": [], "@odata.deltaLink": continuation})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = OutlookConnector(
            personal_account,
            client,
            token_provider=FakeTokenProvider(),
            sleep=fail_sleep,
        )
        first = await connector.fetch(None, 10)
        assert first.next_cursor is not None
        # The stored cursor must round-trip through the same validation on resume.
        second = await connector.fetch(first.next_cursor, 10)

    assert first.messages == []
    assert second.messages == []


async def test_foreign_mailbox_is_rejected_in_graph_key_syntax_too() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "value": [],
                "@odata.nextLink": (
                    "https://graph.microsoft.com/v1.0/users('another%40example.com')"
                    "/mailFolders('inbox')/messages/delta?$skiptoken=opaque"
                ),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = OutlookConnector(
            account(),
            client,
            token_provider=FakeTokenProvider(),
            sleep=fail_sleep,
        )
        with pytest.raises(ProviderResponseError) as caught:
            await connector.fetch(None, 10)

    assert caught.value.code == "invalid_delta_payload"


async def test_percent_encoded_separator_cannot_impersonate_the_configured_folder() -> None:
    personal_account = OutlookAccount(
        provider="outlook",
        id="personal",
        mailbox="person@hotmail.fr",
        auth=MsalDeviceCodeAuth(type="device_code", client_id="public-client-id"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "value": [],
                "@odata.nextLink": (
                    "https://graph.microsoft.com/v1.0/me/"
                    "mailFolders('inbox%2Fmessages%2Fdelta')?$skiptoken=opaque"
                ),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = OutlookConnector(
            personal_account,
            client,
            token_provider=FakeTokenProvider(),
            sleep=fail_sleep,
        )
        with pytest.raises(ProviderResponseError) as caught:
            await connector.fetch(None, 10)

    assert caught.value.code == "invalid_delta_payload"


def graph_message(
    message_id: str,
    *,
    body: str = "Message body",
    content_type: str = "text",
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": message_id,
        "conversationId": f"conversation-{message_id}",
        "subject": f"Subject {message_id}",
        "from": {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
        "toRecipients": [{"emailAddress": {"name": "Bob", "address": "bob@example.com"}}],
        "ccRecipients": [],
        "receivedDateTime": "2026-08-27T10:00:00Z",
        "body": {"contentType": content_type, "content": body},
        "bodyPreview": "Preview",
        "importance": "high",
        "isRead": False,
        "webLink": "https://outlook.office.com/mail/id/opaque",
        "categories": ["Customer"],
        "attachments": attachments or [],
    }


async def test_delta_next_and_delta_links_are_opaque_and_use_immutable_ids() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/messages/delta"):
            if request.url.params.get("$skiptoken") == "next-secret":
                return httpx.Response(
                    200,
                    json={
                        "value": [{"id": "message-2"}],
                        "@odata.deltaLink": (
                            "https://graph.microsoft.com/v1.0/users/"
                            "user%40example.com/mailFolders/inbox/messages/delta"
                            "?$deltatoken=delta-secret"
                        ),
                    },
                )
            if request.url.params.get("$deltatoken") == "delta-secret":
                return httpx.Response(
                    200,
                    json={
                        "value": [],
                        "@odata.deltaLink": (
                            "https://graph.microsoft.com/v1.0/users/"
                            "user%40example.com/mailFolders/inbox/messages/delta"
                            "?$deltatoken=delta-new"
                        ),
                    },
                )
            assert request.url.params["$select"] == "id"
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"id": "message-1"},
                        {"id": "removed-message", "@removed": {"reason": "deleted"}},
                    ],
                    "@odata.nextLink": (
                        "https://graph.microsoft.com/v1.0/users/"
                        "user%40example.com/mailFolders/inbox/messages/delta"
                        "?$skiptoken=next-secret"
                    ),
                },
            )
        if path.endswith("/messages/message-1"):
            return httpx.Response(
                200,
                json=graph_message(
                    "message-1",
                    body=("<p>Visible Graph body</p><script>exfiltrateMailbox()</script>"),
                    content_type="html",
                    attachments=[
                        {
                            "name": "invoice.pdf",
                            "contentType": "application/pdf",
                            "size": 123,
                            "isInline": False,
                            "contentBytes": "must-not-enter-domain",
                            "sourceUrl": "https://attacker.invalid/file",
                        }
                    ],
                ),
            )
        if path.endswith("/messages/message-2"):
            return httpx.Response(200, json=graph_message("message-2", body="Second"))
        raise AssertionError(f"unexpected or unsafe request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = OutlookConnector(
            account(),
            client,
            token_provider=FakeTokenProvider(),
            max_body_chars=200,
            sleep=fail_sleep,
        )
        first = await connector.fetch(None, 10)
        second = await connector.fetch(first.next_cursor, 10)
        third = await connector.fetch(second.next_cursor, 10)

    assert [message.provider_message_id for message in first.messages] == ["message-1"]
    assert [message.provider_message_id for message in second.messages] == ["message-2"]
    assert third.messages == []
    assert first.next_cursor is not None
    assert second.next_cursor is not None
    assert "next-secret" not in first.next_cursor
    assert "delta-secret" not in second.next_cursor
    assert first.messages[0].body_text == "Visible Graph body"
    assert "exfiltrateMailbox" not in first.messages[0].body_text
    attachment = first.messages[0].attachments[0]
    assert (attachment.filename, attachment.content_type, attachment.size, attachment.inline) == (
        "invoice.pdf",
        "application/pdf",
        123,
        False,
    )
    assert not any(request.url.path.endswith("/messages/removed-message") for request in requests)
    assert not any("attachments" in request.url.path for request in requests)
    assert all(request.url.host == "graph.microsoft.com" for request in requests)
    assert all('IdType="ImmutableId"' in request.headers["Prefer"] for request in requests)
    assert all(request.headers["Authorization"] == "Bearer graph-bearer" for request in requests)

    get_requests = [
        request for request in requests if not request.url.path.endswith("/messages/delta")
    ]
    assert all(
        'outlook.body-content-type="text"' in request.headers["Prefer"] for request in get_requests
    )
    assert all("contentBytes" not in request.url.params["$expand"] for request in get_requests)
    assert [request.url.params.get("$skiptoken") for request in requests] == [
        None,
        None,
        "next-secret",
        None,
        None,
    ]


async def test_outlook_body_limit_is_applied_before_domain_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages/delta"):
            return httpx.Response(
                200,
                json={
                    "value": [{"id": "large-message"}],
                    "@odata.deltaLink": (
                        "https://graph.microsoft.com/v1.0/users/"
                        "user%40example.com/mailFolders/inbox/messages/delta"
                        "?$deltatoken=body-limit"
                    ),
                },
            )
        if request.url.path.endswith("/messages/large-message"):
            return httpx.Response(
                200,
                json=graph_message("large-message", body="y" * 10_000),
            )
        raise AssertionError(str(request.url))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = OutlookConnector(
            account(),
            client,
            token_provider=FakeTokenProvider(),
            max_body_chars=6,
            sleep=fail_sleep,
        )
        batch = await connector.fetch(None, 10)

    assert batch.messages[0].body_text == "y" * 6


async def test_delta_410_resets_expired_cursor() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            410,
            json={"error": {"code": "resyncChangesApplyDifferences"}},
        )

    expired = encode_cursor(
        account_id="work",
        provider="outlook",
        state={
            "url": (
                "https://graph.microsoft.com/v1.0/users/"
                "user%40example.com/mailFolders/inbox/messages/delta"
                "?$deltatoken=expired"
            ),
            "pending_ids": [],
        },
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = OutlookConnector(
            account(),
            client,
            token_provider=FakeTokenProvider(),
            sleep=fail_sleep,
        )
        batch = await connector.fetch(expired, 10)

    assert batch.messages == []
    assert batch.next_cursor is None
    assert batch.cursor_reset is True
    assert len(requests) == 1
    assert 'IdType="ImmutableId"' in requests[0].headers["Prefer"]


async def test_delta_404_sync_state_not_found_resets_expired_cursor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            404,
            json={"error": {"code": "syncStateNotFound"}},
        )

    expired = encode_cursor(
        account_id="work",
        provider="outlook",
        state={
            "url": (
                "https://graph.microsoft.com/v1.0/users/"
                "user%40example.com/mailFolders/inbox/messages/delta"
                "?$deltatoken=expired-404"
            ),
            "pending_ids": [],
        },
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = OutlookConnector(
            account(),
            client,
            token_provider=FakeTokenProvider(),
            sleep=fail_sleep,
        )
        batch = await connector.fetch(expired, 10)

    assert batch.messages == []
    assert batch.dead_letters == []
    assert batch.next_cursor is None
    assert batch.cursor_reset is True

    def unrelated_not_found(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(404, json={"error": {"code": "ErrorItemNotFound"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(unrelated_not_found)) as client:
        connector = OutlookConnector(
            account(),
            client,
            token_provider=FakeTokenProvider(),
            sleep=fail_sleep,
        )
        with pytest.raises(ResourceNotFoundError):
            await connector.fetch(expired, 10)


async def test_permanent_message_failures_are_bounded_dead_letters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/messages/delta"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"id": "oversized-message"},
                        {"id": "malformed-message"},
                        {"id": "readable-message"},
                    ],
                    "@odata.deltaLink": (
                        "https://graph.microsoft.com/v1.0/users/"
                        "user%40example.com/mailFolders/inbox/messages/delta"
                        "?$deltatoken=after-dead-letters"
                    ),
                },
            )
        if path.endswith("/messages/oversized-message"):
            return httpx.Response(
                200,
                headers={"Content-Length": str(9 * 1024 * 1024)},
                content=b"{}",
            )
        if path.endswith("/messages/malformed-message"):
            return httpx.Response(200, json={"id": "malformed-message"})
        if path.endswith("/messages/readable-message"):
            return httpx.Response(200, json=graph_message("readable-message", body="safe"))
        raise AssertionError(str(request.url))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = OutlookConnector(
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
        ("malformed-message", "invalid_message_payload"),
    ]
    assert batch.next_cursor is not None


async def test_external_next_link_is_rejected_before_any_follow_up_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "value": [],
                "@odata.nextLink": ("https://attacker.invalid/steal?access_token=provider-secret"),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = OutlookConnector(
            account(),
            client,
            token_provider=FakeTokenProvider(),
            sleep=fail_sleep,
        )
        with pytest.raises(ProviderResponseError) as caught:
            await connector.fetch(None, 10)

    assert caught.value.code == "invalid_delta_payload"
    assert "provider-secret" not in str(caught.value)
    assert len(requests) == 1
    assert requests[0].url.host == "graph.microsoft.com"


async def test_next_link_for_another_mailbox_is_rejected() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "value": [],
                "@odata.nextLink": (
                    "https://graph.microsoft.com/v1.0/users/"
                    "another%40example.com/mailFolders/inbox/messages/delta?$skiptoken=opaque"
                ),
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = OutlookConnector(
            account(),
            client,
            token_provider=FakeTokenProvider(),
            sleep=fail_sleep,
        )
        with pytest.raises(ProviderResponseError) as caught:
            await connector.fetch(None, 10)

    assert caught.value.code == "invalid_delta_payload"
    assert len(requests) == 1


@pytest.mark.parametrize(
    "delta_entries",
    [
        [{"id": "raced-message"}, {"id": "raced-message", "@removed": {}}],
        [{"id": "raced-message", "@removed": {}}, {"id": "raced-message"}],
    ],
)
async def test_delta_upsert_and_tombstone_converge_via_authoritative_get(
    delta_entries: list[dict[str, object]],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages/delta"):
            return httpx.Response(
                200,
                json={
                    "value": delta_entries,
                    "@odata.deltaLink": (
                        "https://graph.microsoft.com/v1.0/users/"
                        "user%40example.com/mailFolders/inbox/messages/delta"
                        "?$deltatoken=after-race"
                    ),
                },
            )
        if request.url.path.endswith("/messages/raced-message"):
            return httpx.Response(200, json=graph_message("raced-message"))
        raise AssertionError(str(request.url))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        connector = OutlookConnector(
            account(),
            client,
            token_provider=FakeTokenProvider(),
            sleep=fail_sleep,
        )
        batch = await connector.fetch(None, 10)

    assert [message.provider_message_id for message in batch.messages] == ["raced-message"]
