"""Configuration : mêmes variables EZER_* que l'ancien service, mêmes règles de validation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.demo.data import DEMO_ACCOUNTS
from app.domain.models import PROVIDERS, Account

EzerMode = Literal["demo", "configured"]

_ACCOUNT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_MAILBOX = re.compile(r"[^\s@]{1,64}@[^\s@]{1,255}")
_CLIENT_ID = re.compile(r"[A-Za-z0-9-]{1,128}")
_MAP_KEY = re.compile(r"[A-Za-z0-9._-]{8,128}")
_DIGITS = re.compile(r"[0-9]+")

DEFAULT_API_KEY = "ezer-demo-key"
DEFAULT_NOMINATIM = "https://nominatim.openstreetmap.org"


def _env(name: str) -> AliasChoices:
    return AliasChoices(name)


def _http_url(name: str, raw: Any, fallback: str | None) -> str | None:
    """Les hôtes cartographiques viennent de l'environnement : l'agent ne fournit jamais d'URL."""
    if raw is None or str(raw).strip() == "":
        return fallback
    value = str(raw).strip()
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - lève ValueError si le port est invalide
    except ValueError as error:
        raise ValueError(f"{name} must be an absolute URL") from error
    if not parts.scheme:
        raise ValueError(f"{name} must be an absolute URL")
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"{name} must be an http(s) URL")
    if not parts.hostname:
        raise ValueError(f"{name} must be an absolute URL")
    normalized = parts._replace(netloc=parts.netloc.lower()).geturl()
    if normalized.endswith("/"):
        normalized = normalized[:-1]
    return normalized


def _integer(name: str, raw: Any, fallback: int, minimum: int, maximum: int) -> int:
    if raw is None or raw == "":
        return fallback
    text = str(raw)
    if not _DIGITS.fullmatch(text):
        raise ValueError(f"{name} must be an integer")
    parsed = int(text)
    if parsed < minimum or parsed > maximum or parsed > 2**53 - 1:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _boolean(name: str, raw: Any, fallback: bool) -> bool:
    if raw is None or raw == "":
        return fallback
    if raw is True or raw == "true":
        return True
    if raw is False or raw == "false":
        return False
    raise ValueError(f"{name} must be true or false")


def _parse_account(value: Any, index: int) -> Account:
    if not isinstance(value, dict):
        raise ValueError(f"EZER_ACCOUNTS_JSON entry {index} must be an object")
    if any(key not in ("id", "provider", "mailbox") for key in value):
        raise ValueError(f"EZER_ACCOUNTS_JSON entry {index} has unknown fields")
    identifier = value.get("id")
    if not isinstance(identifier, str) or not _ACCOUNT_ID.fullmatch(identifier):
        raise ValueError(f"EZER_ACCOUNTS_JSON entry {index} has an invalid id")
    provider = value.get("provider")
    if not isinstance(provider, str) or provider not in PROVIDERS:
        raise ValueError(f"EZER_ACCOUNTS_JSON entry {index} has an invalid provider")
    mailbox = value.get("mailbox")
    if "mailbox" in value and (not isinstance(mailbox, str) or len(mailbox) > 320 or not _MAILBOX.fullmatch(mailbox)):
        raise ValueError(f"EZER_ACCOUNTS_JSON entry {index} has an invalid mailbox")
    return Account(
        id=identifier,
        provider=provider,  # type: ignore[arg-type]
        mailbox=mailbox.lower() if isinstance(mailbox, str) else None,
    )


def configured_accounts(raw: str) -> list[Account]:
    try:
        parsed = json.loads(raw)
    except ValueError as error:
        raise ValueError("EZER_ACCOUNTS_JSON must contain valid JSON") from error
    if not isinstance(parsed, list) or len(parsed) > 100:
        raise ValueError("EZER_ACCOUNTS_JSON must be an array with at most 100 entries")
    accounts = [_parse_account(entry, index) for index, entry in enumerate(parsed)]
    if len({account.id for account in accounts}) != len(accounts):
        raise ValueError("EZER_ACCOUNTS_JSON contains duplicate account ids")
    return accounts


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mode: EzerMode = Field("demo", validation_alias=_env("EZER_MODE"))
    api_key: str | None = Field(None, validation_alias=_env("EZER_API_KEY"))
    host: str = Field("127.0.0.1", validation_alias=_env("EZER_HOST"))
    port: int = Field(8080, validation_alias=_env("EZER_PORT"))
    data_file: Path = Field(Path("./data/ezer.json"), validation_alias=_env("EZER_DATA_FILE"))
    source_file: Path | None = Field(None, validation_alias=_env("EZER_SOURCE_FILE"))
    demo_reset_on_start: bool = Field(False, validation_alias=_env("EZER_DEMO_RESET_ON_START"))
    sync_default_limit: int = Field(50, validation_alias=_env("EZER_SYNC_DEFAULT_LIMIT"))
    sync_max_limit: int = Field(500, validation_alias=_env("EZER_SYNC_MAX_LIMIT"))
    accounts_json: str = Field("[]", validation_alias=_env("EZER_ACCOUNTS_JSON"))
    # Application publique Entra utilisée par le flux device code ; sans elle aucune connexion.
    outlook_client_id: str | None = Field(None, validation_alias=_env("EZER_OUTLOOK_CLIENT_ID"))
    # Les jetons délégués ne partagent jamais le fichier de données du tableau de bord.
    token_file: Path = Field(Path("./data/tokens.json"), validation_alias=_env("EZER_TOKEN_FILE"))
    # Clé MapTiler : géocodage côté serveur, et fond de carte côté navigateur.
    maptiler_key: str | None = Field(None, validation_alias=_env("EZER_MAPTILER_KEY"))
    maptiler_style: str = Field("streets-v2-dark", validation_alias=_env("EZER_MAPTILER_STYLE"))
    # Service de calcul d'itinéraire au protocole OSRM ; à héberger soi-même en production.
    routing_url: str | None = Field(None, validation_alias=_env("EZER_ROUTING_URL"))
    default_origin: str = Field("Paris, France", validation_alias=_env("EZER_DEFAULT_ORIGIN"))
    # Géocodeur sans clé, replié quand MapTiler n'est pas configuré ; vide pour le désactiver.
    nominatim_url: str | None = Field(DEFAULT_NOMINATIM, validation_alias=_env("EZER_NOMINATIM_URL"))
    # Contact envoyé en User-Agent, exigé par la politique d'usage de Nominatim.
    map_contact: str = Field("ezer-agent-ui", validation_alias=_env("EZER_MAP_CONTACT"))
    # Clé Jev (TypeSafe) ; sans client Jev branché, les règles locales s'appliquent.
    typesafe_api_key: str | None = Field(None, validation_alias=_env("TYPESAFE_API_KEY"))
    _accounts: list[Account] = PrivateAttr(default_factory=list)

    @property
    def accounts(self) -> list[Account]:
        return self._accounts

    @field_validator("mode", mode="before")
    @classmethod
    def _check_mode(cls, value: Any) -> Any:
        if value not in ("demo", "configured"):
            raise ValueError("EZER_MODE must be demo or configured")
        return value

    @field_validator("api_key", mode="before")
    @classmethod
    def _check_api_key(cls, value: Any) -> Any:
        if value is not None and not 8 <= len(str(value)) <= 512:
            raise ValueError("EZER_API_KEY must contain between 8 and 512 characters")
        return value

    @field_validator("host", mode="before")
    @classmethod
    def _check_host(cls, value: Any) -> Any:
        return value or "127.0.0.1"

    @field_validator("port", mode="before")
    @classmethod
    def _check_port(cls, value: Any) -> int:
        return _integer("EZER_PORT", value, 8080, 1, 65_535)

    @field_validator("data_file", mode="before")
    @classmethod
    def _check_data_file(cls, value: Any) -> Path:
        return Path(str(value) if value else "./data/ezer.json").resolve()

    @field_validator("source_file", mode="before")
    @classmethod
    def _check_source_file(cls, value: Any) -> Path | None:
        return Path(str(value)).resolve() if value else None

    @field_validator("demo_reset_on_start", mode="before")
    @classmethod
    def _check_reset(cls, value: Any) -> bool:
        return _boolean("EZER_DEMO_RESET_ON_START", value, False)

    @field_validator("sync_default_limit", mode="before")
    @classmethod
    def _check_default_limit(cls, value: Any) -> int:
        return _integer("EZER_SYNC_DEFAULT_LIMIT", value, 50, 1, 500)

    @field_validator("sync_max_limit", mode="before")
    @classmethod
    def _check_max_limit(cls, value: Any) -> int:
        return _integer("EZER_SYNC_MAX_LIMIT", value, 500, 1, 500)

    @field_validator("outlook_client_id", mode="before")
    @classmethod
    def _check_client_id(cls, value: Any) -> str | None:
        client_id = None if value is None else str(value).strip()
        if client_id and not _CLIENT_ID.fullmatch(client_id):
            raise ValueError("EZER_OUTLOOK_CLIENT_ID must be an application identifier")
        return client_id or None

    @field_validator("token_file", mode="before")
    @classmethod
    def _check_token_file(cls, value: Any) -> Path:
        return Path(str(value) if value else "./data/tokens.json").resolve()

    @field_validator("maptiler_key", mode="before")
    @classmethod
    def _check_maptiler_key(cls, value: Any) -> str | None:
        key = None if value is None else str(value).strip()
        if key and not _MAP_KEY.fullmatch(key):
            raise ValueError("EZER_MAPTILER_KEY must be an API key")
        return key or None

    @field_validator("maptiler_style", mode="before")
    @classmethod
    def _check_style(cls, value: Any) -> str:
        return (str(value).strip() if value is not None else "") or "streets-v2-dark"

    @field_validator("routing_url", mode="before")
    @classmethod
    def _check_routing_url(cls, value: Any) -> str | None:
        return _http_url("EZER_ROUTING_URL", value, None)

    @field_validator("default_origin", mode="before")
    @classmethod
    def _check_origin(cls, value: Any) -> str:
        return (str(value).strip() if value is not None else "") or "Paris, France"

    @field_validator("nominatim_url", mode="before")
    @classmethod
    def _check_nominatim(cls, value: Any) -> str | None:
        if value is not None and str(value).strip() == "":
            return None
        return _http_url("EZER_NOMINATIM_URL", value, DEFAULT_NOMINATIM)

    @field_validator("map_contact", mode="before")
    @classmethod
    def _check_contact(cls, value: Any) -> str:
        return ((str(value).strip() if value is not None else "") or "ezer-agent-ui")[:120]

    @field_validator("typesafe_api_key", mode="before")
    @classmethod
    def _check_typesafe(cls, value: Any) -> str | None:
        return (str(value).strip() if value is not None else "") or None

    @model_validator(mode="after")
    def _finalize(self) -> Settings:
        if self.mode == "configured" and self.api_key is None:
            raise ValueError("EZER_API_KEY is required in configured mode")
        if self.api_key is None:
            self.api_key = DEFAULT_API_KEY
        if self.sync_default_limit > self.sync_max_limit:
            raise ValueError("EZER_SYNC_DEFAULT_LIMIT cannot exceed EZER_SYNC_MAX_LIMIT")
        self._accounts = (
            [account.model_copy() for account in DEMO_ACCOUNTS]
            if self.mode == "demo"
            else configured_accounts(self.accounts_json)
        )
        return self
