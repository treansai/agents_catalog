import httpx
import pytest
from app.config import Settings
from app.mailbox.mailbox import HttpMailboxClient, MailboxUnavailableError
from pydantic import ValidationError


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, api_key="long-enough-key", **overrides)  # type: ignore[arg-type]


def test_defaults_and_assistant_configuration() -> None:
    config = settings()
    assert config.port == 8080
    assert config.anthropic_model == "claude-sonnet-5"
    assert config.llm_max_tokens == 4096
    assert config.assistant_configured is False
    full = settings(anthropic_api_key="k", backend_url="http://backend:8080/", backend_api_key="b")
    assert full.backend_url == "http://backend:8080"
    assert full.assistant_configured is True


def test_reads_the_ezer_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EZER_API_KEY", "long-enough-key")
    monkeypatch.setenv("EZER_PORT", "8081")
    monkeypatch.setenv("EZER_LLM_TIMEOUT_SECONDS", "")
    config = Settings(_env_file=None)
    assert config.port == 8081
    assert config.llm_timeout_seconds == 60


@pytest.mark.parametrize(
    "overrides",
    [
        {"api_key": "short"},
        {"port": "abc"},
        {"port": "70000"},
        {"llm_max_tokens": "10"},
        {"anthropic_model": "gpt-4"},
        {"backend_url": "ftp://backend"},
        {"backend_url": "not a url"},
    ],
)
def test_rejects_invalid_configuration(overrides: dict[str, str]) -> None:
    values = {"api_key": "long-enough-key", **overrides}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)  # type: ignore[arg-type]


async def test_mailbox_client_maps_backend_statuses_to_codes() -> None:
    status_code = {"value": 401}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code["value"], json={})

    client = HttpMailboxClient("http://backend:8080", "key", httpx.MockTransport(handler))
    for status, code in [
        (401, "backend_unauthorized"),
        (404, "account_not_found"),
        (422, "mailbox_not_available"),
        (500, "backend_request_failed"),
        (200, "invalid_backend_payload"),
    ]:
        status_code["value"] = status
        with pytest.raises(MailboxUnavailableError) as raised:
            await client.list_recent("outlook-perso", 5)
        assert raised.value.code == code


async def test_mailbox_client_validates_identifiers_and_clamps_top() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"messages": [{"message_id": "bad id"}]})

    client = HttpMailboxClient("http://backend:8080", "key", httpx.MockTransport(handler))
    with pytest.raises(MailboxUnavailableError) as raised:
        await client.list_recent("../x", 5)
    assert raised.value.code == "invalid_account_id"
    with pytest.raises(MailboxUnavailableError) as raised:
        await client.get_message("outlook-perso", "bad id")
    assert raised.value.code == "invalid_message_id"

    assert await client.list_recent("outlook-perso", 500) == []
    assert seen[0].url.params["top"] == "25"
    assert seen[0].url.params["order"] == "desc"
    assert seen[0].headers["x-api-key"] == "key"
    assert await client.search("outlook-perso", "   ", 5) == []
    assert len(seen) == 1


async def test_mailbox_client_reports_an_unreachable_backend() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = HttpMailboxClient("http://backend:8080", "key", httpx.MockTransport(handler))
    with pytest.raises(MailboxUnavailableError) as raised:
        await client.stats("outlook-perso")
    assert raised.value.code == "backend_unreachable"
