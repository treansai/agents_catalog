import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from domain._validation import require_finite_range, require_non_blank


@dataclass(frozen=True, slots=True)
class AgentConfig:
    prompt_template: str
    model_id: str
    temperature: float
    top_p: float
    max_tokens: int
    tools_version: str
    context_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_config(self)


def _validate_context_fields(fields: tuple[str, ...]) -> None:
    if any(not field.strip() for field in fields):
        raise ValueError("context_fields must not contain blank values")
    if len(fields) != len(set(fields)):
        raise ValueError("context_fields must not contain duplicates")


def _validate_config(config: AgentConfig) -> None:
    require_non_blank(config.prompt_template, "prompt_template")
    require_non_blank(config.model_id, "model_id")
    require_non_blank(config.tools_version, "tools_version")
    require_finite_range(config.temperature, 0.0, 2.0, "temperature")
    require_finite_range(config.top_p, 0.0, 1.0, "top_p")
    if config.max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    _validate_context_fields(config.context_fields)


def config_to_payload(config: AgentConfig) -> dict[str, object]:
    return {
        "prompt_template": config.prompt_template,
        "model_id": config.model_id,
        "temperature": config.temperature,
        "top_p": config.top_p,
        "max_tokens": config.max_tokens,
        "tools_version": config.tools_version,
        "context_fields": list(config.context_fields),
    }


def canonical_config_json(payload: Mapping[str, object]) -> bytes:
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return serialized.encode("utf-8")


def hash_config_payload(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_config_json(payload)).hexdigest()


def config_hash(config: AgentConfig) -> str:
    return hash_config_payload(config_to_payload(config))
