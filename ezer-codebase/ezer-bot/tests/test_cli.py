from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

import ezer.cli as cli
from ezer.cli import (
    ChallengeCallback,
    InteractiveOutlookTokenProvider,
    authenticate_outlook,
    build_parser,
)
from ezer.config import Settings
from ezer.connectors.base import ConnectorConfigurationError


def _outlook_settings(
    *,
    auth: dict[str, str] | None = None,
    environment: Literal["development", "test", "staging", "production"] = "development",
    cache_dir: Path = Path("var/msal-cache"),
    cache_key: str | None = None,
) -> Settings:
    return Settings.model_validate(
        {
            "environment": environment,
            "anthropic_api_key": None,
            "accounts_file": None,
            "accounts_json": json.dumps(
                [
                    {
                        "provider": "outlook",
                        "id": "personal",
                        "mailbox": "person@hotmail.fr",
                        "auth": auth
                        or {
                            "type": "device_code",
                            "client_id": "public-client-id",
                        },
                    }
                ]
            ),
            "msal_cache_dir": cache_dir,
            "msal_cache_aes_key": cache_key,
        }
    )


@dataclass(frozen=True, slots=True)
class _Challenge:
    verification_uri: str = "https://microsoft.com/devicelogin"
    user_code: str = "ABCD-EFGH"


class _Provider:
    def __init__(self, challenge: _Challenge | None = None) -> None:
        self.challenge = challenge or _Challenge()
        self.interactive_calls = 0

    async def authenticate_interactively(self, on_challenge: ChallengeCallback) -> object:
        self.interactive_calls += 1
        callback_result = on_challenge(self.challenge)
        if inspect.isawaitable(callback_result):
            await callback_result
        return object()


def test_api_cli_defaults_are_local_only() -> None:
    arguments = build_parser().parse_args(["api"])

    assert arguments.host == "127.0.0.1"
    assert arguments.port == 8080


def test_api_cli_rejects_invalid_port() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["api", "--port", "70000"])


def test_outlook_auth_cli_requires_explicit_account() -> None:
    arguments = build_parser().parse_args(["auth", "outlook", "--account", "personal"])

    assert arguments.command == "auth"
    assert arguments.auth_provider == "outlook"
    assert arguments.account == "personal"

    with pytest.raises(SystemExit):
        build_parser().parse_args(["auth", "outlook"])


async def test_outlook_auth_is_interactive_without_anthropic_or_database(
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = _outlook_settings()
    provider = _Provider()
    factory_call: dict[str, object] = {}

    def factory(
        *,
        account_id: str,
        mailbox: str,
        client_id: str,
        cache_path: Path,
        cache_encryption_key: bytes | None,
    ) -> InteractiveOutlookTokenProvider:
        factory_call.update(
            account_id=account_id,
            mailbox=mailbox,
            client_id=client_id,
            cache_path=cache_path,
            cache_encryption_key=cache_encryption_key,
        )
        return provider

    await authenticate_outlook(settings, "personal", provider_factory=factory)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ("Open https://microsoft.com/devicelogin and enter code ABCD-EFGH.\n")
    assert provider.interactive_calls == 1
    assert settings.anthropic_api_key is None
    assert factory_call == {
        "account_id": "personal",
        "mailbox": "person@hotmail.fr",
        "client_id": "public-client-id",
        "cache_path": Path("var/msal-cache/personal.bin"),
        "cache_encryption_key": None,
    }


async def test_outlook_auth_accepts_legacy_personal_client_credentials() -> None:
    settings = _outlook_settings(
        auth={
            "type": "client_credentials",
            "tenant_id": "legacy-tenant",
            "client_id": "legacy-public-client-id",
            "client_secret": "must-not-be-used",
        }
    )
    provider = _Provider()
    received_client_ids: list[str] = []

    def factory(
        *,
        account_id: str,
        mailbox: str,
        client_id: str,
        cache_path: Path,
        cache_encryption_key: bytes | None,
    ) -> InteractiveOutlookTokenProvider:
        del account_id, mailbox, cache_path, cache_encryption_key
        received_client_ids.append(client_id)
        return provider

    await authenticate_outlook(settings, "personal", provider_factory=factory)

    assert received_client_ids == ["legacy-public-client-id"]


@pytest.mark.parametrize(
    ("verification_uri", "expected"),
    [
        ("https://www.microsoft.com/link", "https://www.microsoft.com/link"),
        ("https://microsoft.com/link", "https://microsoft.com/link"),
        ("https://www.microsoft.com/devicelogin/", "https://www.microsoft.com/devicelogin"),
    ],
)
async def test_outlook_auth_prints_the_page_microsoft_returned(
    capsys: pytest.CaptureFixture[str],
    verification_uri: str,
    expected: str,
) -> None:
    settings = _outlook_settings()
    provider = _Provider(_Challenge(verification_uri=verification_uri))

    def factory(
        *,
        account_id: str,
        mailbox: str,
        client_id: str,
        cache_path: Path,
        cache_encryption_key: bytes | None,
    ) -> InteractiveOutlookTokenProvider:
        del account_id, mailbox, client_id, cache_path, cache_encryption_key
        return provider

    await authenticate_outlook(settings, "personal", provider_factory=factory)

    assert capsys.readouterr().err == f"Open {expected} and enter code ABCD-EFGH.\n"


async def test_outlook_auth_rejects_unknown_account_and_untrusted_challenge() -> None:
    settings = _outlook_settings()

    with pytest.raises(ConnectorConfigurationError) as unknown:
        await authenticate_outlook(settings, "missing")
    assert unknown.value.code == "outlook_account_not_configured"

    provider = _Provider(
        _Challenge(
            verification_uri="https://example.invalid/device",
            user_code="ATTACK-CODE",
        )
    )

    def factory(
        *,
        account_id: str,
        mailbox: str,
        client_id: str,
        cache_path: Path,
        cache_encryption_key: bytes | None,
    ) -> InteractiveOutlookTokenProvider:
        del account_id, mailbox, client_id, cache_path, cache_encryption_key
        return provider

    with pytest.raises(ConnectorConfigurationError) as invalid:
        await authenticate_outlook(settings, "personal", provider_factory=factory)
    assert invalid.value.code == "invalid_device_code_challenge"


async def test_outlook_auth_enforces_encrypted_absolute_cache_in_production() -> None:
    settings = _outlook_settings(environment="production")

    def factory(
        *,
        account_id: str,
        mailbox: str,
        client_id: str,
        cache_path: Path,
        cache_encryption_key: bytes | None,
    ) -> InteractiveOutlookTokenProvider:
        del account_id, mailbox, client_id, cache_path, cache_encryption_key
        raise AssertionError("an invalid production cache must fail before provider construction")

    with pytest.raises(ConnectorConfigurationError) as invalid_path:
        await authenticate_outlook(settings, "personal", provider_factory=factory)
    assert invalid_path.value.code == "msal_cache_path_must_be_absolute"


def test_main_dispatches_outlook_auth_without_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = _outlook_settings()
    calls: list[tuple[Settings, str]] = []

    async def fake_authenticate(value: Settings, account_id: str) -> None:
        calls.append((value, account_id))

    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "configure_logging", lambda level: None)
    monkeypatch.setattr(cli, "authenticate_outlook", fake_authenticate)

    assert cli.main(["auth", "outlook", "--account", "personal"]) == 0
    assert calls == [(settings, "personal")]
    assert json.loads(capsys.readouterr().out) == {
        "status": "authenticated",
        "provider": "outlook",
        "account_id": "personal",
    }
