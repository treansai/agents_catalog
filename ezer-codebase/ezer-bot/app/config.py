"""Configuration read from the EZER_* environment variables (and an optional .env file)."""

import re
from typing import Any
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DIGITS = re.compile(r"[0-9]+")

_INTEGER_BOUNDS: dict[str, tuple[str, int, int, int]] = {
    # field: (environment name, default, minimum, maximum)
    "port": ("EZER_PORT", 8080, 1, 65_535),
    "llm_max_tokens": ("EZER_LLM_MAX_TOKENS", 4096, 256, 128_000),
    "llm_timeout_seconds": ("EZER_LLM_TIMEOUT_SECONDS", 60, 1, 600),
}


def _blank_to_none(value: Any) -> str | None:
    if value is None:
        return None
    trimmed = str(value).strip()
    return trimmed or None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EZER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str
    host: str = "127.0.0.1"
    port: int = 8080
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"
    llm_max_tokens: int = 4096
    llm_timeout_seconds: int = 60
    backend_url: str | None = None
    backend_api_key: str | None = None

    @field_validator("api_key", mode="before")
    @classmethod
    def _check_api_key(cls, value: Any) -> Any:
        if not isinstance(value, str) or len(value) < 8 or len(value) > 512:
            raise ValueError("EZER_API_KEY must contain between 8 and 512 characters")
        return value

    @field_validator("host", mode="before")
    @classmethod
    def _default_host(cls, value: Any) -> Any:
        return value or "127.0.0.1"

    @field_validator("port", "llm_max_tokens", "llm_timeout_seconds", mode="before")
    @classmethod
    def _check_integer(cls, value: Any, info: Any) -> int:
        name, default, minimum, maximum = _INTEGER_BOUNDS[info.field_name]
        if value is None or value == "":
            return default
        if isinstance(value, bool):
            raise ValueError(f"{name} must be an integer")
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, str) and _DIGITS.fullmatch(value):
            parsed = int(value)
        else:
            raise ValueError(f"{name} must be an integer")
        if parsed < minimum or parsed > maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
        return parsed

    @field_validator("anthropic_api_key", "backend_api_key", mode="before")
    @classmethod
    def _strip_secret(cls, value: Any) -> str | None:
        return _blank_to_none(value)

    @field_validator("anthropic_model", mode="before")
    @classmethod
    def _check_model(cls, value: Any) -> str:
        model = _blank_to_none(value) or "claude-sonnet-5"
        if not model.startswith("claude-"):
            raise ValueError("EZER_ANTHROPIC_MODEL must be an Anthropic Claude API model ID")
        return model

    @field_validator("backend_url", mode="before")
    @classmethod
    def _check_backend_url(cls, value: Any) -> str | None:
        trimmed = _blank_to_none(value)
        if trimmed is None:
            return None
        try:
            parsed = urlsplit(trimmed)
        except ValueError as error:
            raise ValueError("EZER_BACKEND_URL must be an absolute URL") from error
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("EZER_BACKEND_URL must be an absolute URL")
        if parsed.scheme not in ("http", "https"):
            raise ValueError("EZER_BACKEND_URL must be an http(s) URL")
        return trimmed[:-1] if trimmed.endswith("/") else trimmed

    @property
    def assistant_configured(self) -> bool:
        return (
            self.anthropic_api_key is not None
            and self.backend_url is not None
            and self.backend_api_key is not None
        )
