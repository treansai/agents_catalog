from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ezer.config import ClientCredentialsAuth, MsalDeviceCodeAuth, Settings


def test_loads_discriminated_accounts_and_redacts_secrets() -> None:
    settings = Settings(
        _env_file=None,
        anthropic_api_key="anthropic-secret",
        accounts_json=json.dumps(
            [
                {
                    "provider": "gmail",
                    "id": "personal",
                    "auth": {
                        "type": "refresh_token",
                        "client_id": "client",
                        "client_secret": "client-secret",
                        "refresh_token": "refresh-secret",
                    },
                },
                {
                    "provider": "outlook",
                    "id": "work",
                    "mailbox": "user@example.com",
                    "auth": {
                        "type": "client_credentials",
                        "tenant_id": "tenant-id",
                        "client_id": "client",
                        "client_secret": "client-secret",
                    },
                },
            ]
        ),
    )

    accounts = settings.load_accounts()

    assert [account.id for account in accounts] == ["personal", "work"]
    assert isinstance(accounts[1].auth, ClientCredentialsAuth)
    assert "refresh-secret" not in repr(accounts)
    assert "anthropic-secret" not in repr(settings)


def test_duplicate_account_ids_are_rejected() -> None:
    account = {
        "provider": "gmail",
        "id": "duplicate",
        "auth": {"type": "access_token", "access_token": "temporary"},
    }
    settings = Settings(_env_file=None, accounts_json=json.dumps([account, account]))

    with pytest.raises(ValueError, match="unique"):
        settings.load_accounts()


def test_outlook_refresh_token_requires_tenant() -> None:
    with pytest.raises(ValidationError, match="tenant_id"):
        Settings(
            _env_file=None,
            accounts_json=json.dumps(
                [
                    {
                        "provider": "outlook",
                        "id": "work",
                        "mailbox": "user@example.com",
                        "auth": {
                            "type": "refresh_token",
                            "client_id": "client",
                            "client_secret": "secret",
                            "refresh_token": "refresh",
                        },
                    }
                ]
            ),
        ).load_accounts()


def test_personal_outlook_device_code_auth_is_secretless_and_consumers_only() -> None:
    settings = Settings(
        _env_file=None,
        accounts_json=json.dumps(
            [
                {
                    "provider": "outlook",
                    "id": "personal",
                    "mailbox": "person@hotmail.fr",
                    "auth": {
                        "type": "device_code",
                        "client_id": "public-client-id",
                    },
                }
            ]
        ),
    )

    account = settings.load_accounts()[0]
    assert isinstance(account.auth, MsalDeviceCodeAuth)
    assert account.auth.tenant_id == "consumers"

    invalid = json.loads(settings.accounts_json.get_secret_value())
    invalid[0]["auth"]["tenant_id"] = "organizations"
    with pytest.raises(ValidationError, match="consumers"):
        Settings(_env_file=None, accounts_json=json.dumps(invalid)).load_accounts()


def test_legacy_personal_client_credentials_are_normalized_to_device_code() -> None:
    settings = Settings(
        _env_file=None,
        accounts_json=json.dumps(
            [
                {
                    "provider": "outlook",
                    "id": "legacy-personal",
                    "mailbox": "person@hotmail.co.uk",
                    "auth": {
                        "type": "client_credentials",
                        "tenant_id": "old-organization-tenant",
                        "client_id": "public-client-id",
                        "client_secret": "unused-secret",
                    },
                }
            ]
        ),
    )

    account = settings.load_accounts()[0]

    assert isinstance(account.auth, MsalDeviceCodeAuth)
    assert account.auth.client_id == "public-client-id"
    assert "unused-secret" not in repr(account)


def test_gmail_rejects_query_that_incremental_history_cannot_preserve() -> None:
    settings = Settings(
        _env_file=None,
        accounts_json=json.dumps(
            [
                {
                    "provider": "gmail",
                    "id": "personal",
                    "query": "from:billing@example.com",
                    "auth": {"type": "access_token", "access_token": "temporary"},
                }
            ]
        ),
    )

    with pytest.raises(ValidationError, match="in:inbox"):
        settings.load_accounts()


def test_production_rejects_sqlite_and_static_tokens() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        anthropic_api_key="secret",
        api_key="api-secret",
        accounts_json=json.dumps(
            [
                {
                    "provider": "gmail",
                    "id": "personal",
                    "auth": {"type": "access_token", "access_token": "temporary"},
                }
            ]
        ),
    )

    with pytest.raises(ValueError, match="PostgreSQL"):
        settings.validate_for_role("api")


def test_production_requires_a_strong_api_key() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        anthropic_api_key="anthropic-secret",
        api_key="too-short",
        database_url="postgresql://ezer:secret@postgres/ezer",
        checkpoint_database_url="postgresql://ezer:secret@postgres/ezer",
        checkpoint_aes_key="0123456789abcdef0123456789abcdef",
        accounts_json=json.dumps(
            [
                {
                    "provider": "outlook",
                    "id": "work",
                    "mailbox": "user@example.com",
                    "auth": {
                        "type": "client_credentials",
                        "tenant_id": "tenant-id",
                        "client_id": "client",
                        "client_secret": "client-secret",
                    },
                }
            ]
        ),
    )

    with pytest.raises(ValueError, match="32 to 512 bytes"):
        settings.validate_for_role("api")


@pytest.mark.parametrize(
    ("auth", "error"),
    [
        ({"type": "access_token", "access_token": "   "}, "access_token"),
        (
            {
                "type": "refresh_token",
                "client_id": "client",
                "client_secret": " ",
                "refresh_token": "refresh",
            },
            "client_secret",
        ),
    ],
)
def test_empty_oauth_secrets_are_rejected(auth: dict[str, str], error: str) -> None:
    settings = Settings(
        _env_file=None,
        accounts_json=json.dumps([{"provider": "gmail", "id": "personal", "auth": auth}]),
    )

    with pytest.raises(ValidationError, match=error):
        settings.load_accounts()


def _valid_production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "environment": "production",
        "anthropic_api_key": "anthropic-secret",
        "api_key": "a" * 32,
        "database_url": "postgresql://ezer:secret@postgres/ezer?sslmode=require",
        "checkpoint_database_url": "postgresql://ezer:secret@postgres/ezer?sslmode=require",
        "checkpoint_aes_key": "0123456789abcdef0123456789abcdef",
        "accounts_json": json.dumps(
            [
                {
                    "provider": "outlook",
                    "id": "work",
                    "mailbox": "user@example.com",
                    "auth": {
                        "type": "client_credentials",
                        "tenant_id": "tenant-id",
                        "client_id": "client",
                        "client_secret": "client-secret",
                    },
                }
            ]
        ),
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_production_rejects_external_llm_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    with pytest.raises(ValueError, match="tracing is forbidden"):
        _valid_production_settings().validate_for_role("api")


@pytest.mark.parametrize(
    ("override", "error"),
    [
        (
            {"database_url": "postgresql://ezer:secret@postgres/ezer?sslmode=disable"},
            "must not disable TLS",
        ),
        (
            {"checkpoint_database_url": "sqlite:///checkpoints.db"},
            "must use PostgreSQL",
        ),
    ],
)
def test_production_validates_both_database_transports(
    override: dict[str, object],
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        _valid_production_settings(**override).validate_for_role("api")
