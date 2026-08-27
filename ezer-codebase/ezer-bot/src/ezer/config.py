"""Typed configuration loaded from environment variables and secret JSON files."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, Field, SecretStr, TypeAdapter, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StaticTokenAuth(BaseModel):
    """Short-lived bearer token; intended for development and smoke tests only."""

    type: Literal["access_token"]
    access_token: SecretStr

    @model_validator(mode="after")
    def reject_empty_token(self) -> StaticTokenAuth:
        if not self.access_token.get_secret_value().strip():
            raise ValueError("access_token must not be empty")
        return self


class RefreshTokenAuth(BaseModel):
    """OAuth refresh-token credentials for delegated mailbox access."""

    type: Literal["refresh_token"]
    client_id: str = Field(min_length=1)
    client_secret: SecretStr
    refresh_token: SecretStr
    tenant_id: str | None = None

    @model_validator(mode="after")
    def reject_empty_credentials(self) -> RefreshTokenAuth:
        if not self.client_id.strip():
            raise ValueError("client_id must not be blank")
        if not self.client_secret.get_secret_value().strip():
            raise ValueError("client_secret must not be empty")
        if not self.refresh_token.get_secret_value().strip():
            raise ValueError("refresh_token must not be empty")
        return self


class ClientCredentialsAuth(BaseModel):
    """Microsoft Entra application credentials for organization mailboxes."""

    type: Literal["client_credentials"]
    tenant_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9._-]+$")
    client_id: str = Field(min_length=1)
    client_secret: SecretStr

    @model_validator(mode="after")
    def reject_empty_credentials(self) -> ClientCredentialsAuth:
        if not self.client_id.strip():
            raise ValueError("client_id must not be blank")
        if not self.client_secret.get_secret_value().strip():
            raise ValueError("client_secret must not be empty")
        return self


class MsalDeviceCodeAuth(BaseModel):
    """Interactive delegated authentication for personal Microsoft mailboxes."""

    type: Literal["device_code"]
    client_id: str = Field(min_length=1)
    tenant_id: Literal["consumers"] = "consumers"

    @model_validator(mode="after")
    def reject_blank_client_id(self) -> MsalDeviceCodeAuth:
        if not self.client_id.strip():
            raise ValueError("client_id must not be blank")
        return self


_MICROSOFT_PERSONAL_EXACT_DOMAINS = frozenset(
    {
        "msn.com",
        "outlook.com",
        "outlook.fr",
    }
)
_MICROSOFT_PERSONAL_DOMAIN_PREFIXES = ("hotmail.", "live.")


def is_personal_microsoft_mailbox(mailbox: str) -> bool:
    """Recognize well-known consumer mailbox domains for safe legacy migration."""

    domain = mailbox.rsplit("@", 1)[-1].strip().casefold()
    return domain in _MICROSOFT_PERSONAL_EXACT_DOMAINS or domain.startswith(
        _MICROSOFT_PERSONAL_DOMAIN_PREFIXES
    )


AuthConfig = Annotated[
    StaticTokenAuth | RefreshTokenAuth | ClientCredentialsAuth | MsalDeviceCodeAuth,
    Field(discriminator="type"),
]


class GmailAccount(BaseModel):
    provider: Literal["gmail"]
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    mailbox: str = Field(default="me", min_length=1, max_length=320)
    # Incremental Gmail history can be scoped by label but cannot replay an arbitrary search
    # expression. Keeping this fixed prevents bootstrap and incremental sync from diverging.
    query: Literal["in:inbox"] = "in:inbox"
    auth: Annotated[StaticTokenAuth | RefreshTokenAuth, Field(discriminator="type")]

    @model_validator(mode="after")
    def reject_microsoft_tenant(self) -> GmailAccount:
        if isinstance(self.auth, RefreshTokenAuth) and self.auth.tenant_id:
            raise ValueError("tenant_id is not valid for Gmail OAuth")
        return self


class OutlookAccount(BaseModel):
    provider: Literal["outlook"]
    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    mailbox: str = Field(min_length=1, max_length=320)
    folder: str = Field(default="inbox", min_length=1, max_length=256)
    auth: AuthConfig

    @model_validator(mode="after")
    def require_tenant_for_refresh(self) -> OutlookAccount:
        # Older Ezer examples used client credentials for every Outlook mailbox. Microsoft does
        # not support that daemon flow for personal accounts, so normalize those known consumer
        # domains to the explicit public-client device flow. Only the public client ID survives;
        # tenant and client secret are intentionally ignored.
        if isinstance(self.auth, ClientCredentialsAuth) and is_personal_microsoft_mailbox(
            self.mailbox
        ):
            self.auth = MsalDeviceCodeAuth(
                type="device_code",
                client_id=self.auth.client_id,
            )
        if isinstance(self.auth, RefreshTokenAuth) and not self.auth.tenant_id:
            raise ValueError("tenant_id is required for Outlook refresh-token auth")
        return self


EmailAccount = Annotated[GmailAccount | OutlookAccount, Field(discriminator="provider")]
_ACCOUNT_LIST = TypeAdapter(list[EmailAccount])
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def _validate_production_database_url(value: SecretStr, *, setting_name: str) -> None:
    raw = value.get_secret_value()
    if not raw.startswith(("postgresql://", "postgres://")):
        raise ValueError(f"production {setting_name} must use PostgreSQL")
    parsed = urlsplit(raw)
    if parsed.hostname is None or not parsed.path or parsed.fragment:
        raise ValueError(f"production {setting_name} is not a valid PostgreSQL URL")
    ssl_modes = parse_qs(parsed.query).get("sslmode", [])
    if any(mode.casefold() == "disable" for mode in ssl_modes):
        raise ValueError(f"production {setting_name} must not disable TLS")


def _external_tracing_enabled() -> bool:
    return any(
        os.getenv(name, "").strip().casefold() in _TRUTHY_ENV_VALUES
        for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2")
    )


class Settings(BaseSettings):
    """Runtime settings. Secrets are redacted by Pydantic and never logged."""

    model_config = SettingsConfigDict(
        env_prefix="EZER_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    api_key: SecretStr | None = None

    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-5"
    llm_max_tokens: int = Field(default=4096, ge=256, le=128_000)
    llm_timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    llm_max_attempts: int = Field(default=3, ge=1, le=6)

    # Accès mail de l'assistant : il passe par le backend, jamais par Microsoft directement.
    backend_url: str | None = None
    backend_api_key: SecretStr | None = None

    accounts_file: Path | None = None
    accounts_json: SecretStr | None = None

    database_url: SecretStr = SecretStr("sqlite:///./var/ezer.db")
    checkpoint_database_url: SecretStr | None = None
    checkpoint_aes_key: SecretStr | None = None
    msal_cache_dir: Path = Path("./var/msal-cache")
    msal_cache_aes_key: SecretStr | None = None
    auto_migrate: bool = True
    db_pool_min_size: int = Field(default=1, ge=1, le=20)
    db_pool_max_size: int = Field(default=10, ge=1, le=100)

    poll_interval_seconds: float = Field(default=60.0, ge=5.0, le=86_400.0)
    sync_page_size: int = Field(default=50, ge=1, le=500)
    processing_concurrency: int = Field(default=4, ge=1, le=32)
    provider_concurrency: int = Field(default=4, ge=1, le=16)
    max_body_chars: int = Field(default=40_000, ge=1_000, le=500_000)
    processing_lease_seconds: int = Field(default=900, ge=30, le=86_400)
    processing_max_attempts: int = Field(default=5, ge=1, le=20)

    pipeline_version: str = Field(default="2026-08-27.1", min_length=1, max_length=64)
    output_language: str = Field(default="fr", min_length=2, max_length=16)

    @field_validator("anthropic_model")
    @classmethod
    def validate_model_name(cls, value: str) -> str:
        if not value.startswith("claude-"):
            raise ValueError("use an Anthropic Claude API model ID, e.g. claude-sonnet-5")
        return value

    @model_validator(mode="after")
    def validate_pool(self) -> Settings:
        if self.db_pool_max_size < self.db_pool_min_size:
            raise ValueError("db_pool_max_size must be >= db_pool_min_size")
        return self

    def load_accounts(self) -> list[EmailAccount]:
        if self.accounts_file and self.accounts_json:
            raise ValueError("configure either EZER_ACCOUNTS_FILE or EZER_ACCOUNTS_JSON, not both")
        if self.accounts_file:
            raw = self.accounts_file.read_text(encoding="utf-8")
        elif self.accounts_json:
            raw = self.accounts_json.get_secret_value()
        else:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("email account configuration is not valid JSON") from exc
        accounts = _ACCOUNT_LIST.validate_python(data)
        ids = [account.id for account in accounts]
        if len(ids) != len(set(ids)):
            raise ValueError("email account IDs must be unique")
        return accounts

    def validate_for_role(self, role: Literal["api", "worker", "sync"]) -> None:
        accounts = self.load_accounts()
        if not self.anthropic_api_key or not self.anthropic_api_key.get_secret_value().strip():
            raise ValueError("EZER_ANTHROPIC_API_KEY is required")
        if not accounts:
            raise ValueError("at least one email account must be configured")
        if role == "api" and not self.api_key:
            raise ValueError("EZER_API_KEY is required for the API")
        if self.environment == "production":
            _validate_production_database_url(
                self.database_url,
                setting_name="EZER_DATABASE_URL",
            )
            if not self.checkpoint_database_url:
                raise ValueError("production requires EZER_CHECKPOINT_DATABASE_URL")
            _validate_production_database_url(
                self.checkpoint_database_url,
                setting_name="EZER_CHECKPOINT_DATABASE_URL",
            )
            if any(isinstance(account.auth, StaticTokenAuth) for account in accounts):
                raise ValueError("static access tokens are forbidden in production")
            if role == "api" and self.api_key:
                api_key_bytes = self.api_key.get_secret_value().encode("utf-8")
                if not 32 <= len(api_key_bytes) <= 512:
                    raise ValueError("production EZER_API_KEY must contain 32 to 512 bytes")
            if _external_tracing_enabled():
                raise ValueError("LangSmith/LangChain tracing is forbidden in production")
            if self.checkpoint_database_url:
                if not self.checkpoint_aes_key:
                    raise ValueError("production checkpoints require EZER_CHECKPOINT_AES_KEY")
                if len(self.checkpoint_aes_key.get_secret_value().encode()) not in (16, 24, 32):
                    raise ValueError("EZER_CHECKPOINT_AES_KEY must encode to 16, 24, or 32 bytes")
            if any(isinstance(account.auth, MsalDeviceCodeAuth) for account in accounts):
                if not self.msal_cache_dir.is_absolute():
                    raise ValueError("production EZER_MSAL_CACHE_DIR must be an absolute path")
                if not self.msal_cache_aes_key:
                    raise ValueError("production device-code auth requires EZER_MSAL_CACHE_AES_KEY")
                if len(self.msal_cache_aes_key.get_secret_value().encode()) not in (16, 24, 32):
                    raise ValueError("EZER_MSAL_CACHE_AES_KEY must encode to 16, 24, or 32 bytes")
