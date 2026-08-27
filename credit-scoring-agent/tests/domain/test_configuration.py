from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace

import pytest

from domain.configuration import (
    AgentConfig,
    canonical_config_json,
    config_hash,
    config_to_payload,
    hash_config_payload,
)

BASE_CONFIG = AgentConfig(
    prompt_template="Décider: {dossier}",
    model_id="mistral-nemo-instruct-2407",
    temperature=0.0,
    top_p=1.0,
    max_tokens=256,
    tools_version="1.0.0",
    context_fields=("revenu_mensuel", "charges_mensuelles"),
)
EXPECTED_JSON = (
    '{"context_fields":["revenu_mensuel","charges_mensuelles"],'
    '"max_tokens":256,"model_id":"mistral-nemo-instruct-2407",'
    '"prompt_template":"Décider: {dossier}","temperature":0.0,'
    '"tools_version":"1.0.0","top_p":1.0}'
)


def test_agent_config_is_immutable() -> None:
    field_name = "model_id"
    with pytest.raises(FrozenInstanceError):
        setattr(BASE_CONFIG, field_name, "another-model")


def test_context_fields_are_immutable() -> None:
    field_name = "context_fields"
    with pytest.raises(FrozenInstanceError):
        setattr(BASE_CONFIG, field_name, ("code_postal",))


def test_canonical_json_is_compact_sorted_and_utf8() -> None:
    encoded = canonical_config_json(config_to_payload(BASE_CONFIG))
    assert encoded.decode("utf-8") == EXPECTED_JSON
    assert "Décider" in encoded.decode("utf-8")


def test_config_hash_matches_golden_digest() -> None:
    expected = "c50dfdf1161cc9b66c204638b4d41430df9eb886cf97aa8b189632b177379548"
    assert config_hash(BASE_CONFIG) == expected


def test_config_hash_ignores_object_key_order() -> None:
    payload = config_to_payload(BASE_CONFIG)
    reversed_payload = dict(reversed(tuple(payload.items())))
    assert hash_config_payload(payload) == hash_config_payload(reversed_payload)


def test_context_field_order_remains_significant() -> None:
    reordered = replace(
        BASE_CONFIG, context_fields=tuple(reversed(BASE_CONFIG.context_fields))
    )
    assert config_hash(BASE_CONFIG) != config_hash(reordered)


def test_canonical_json_rejects_non_finite_numbers() -> None:
    with pytest.raises(ValueError, match="JSON compliant"):
        canonical_config_json({"temperature": float("nan")})


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: replace(BASE_CONFIG, prompt_template=" "), "prompt_template"),
        (lambda: replace(BASE_CONFIG, model_id=""), "model_id"),
        (lambda: replace(BASE_CONFIG, tools_version="\t"), "tools_version"),
        (lambda: replace(BASE_CONFIG, temperature=float("nan")), "temperature"),
        (lambda: replace(BASE_CONFIG, temperature=-0.1), "temperature"),
        (lambda: replace(BASE_CONFIG, temperature=2.1), "temperature"),
        (lambda: replace(BASE_CONFIG, top_p=-0.1), "top_p"),
        (lambda: replace(BASE_CONFIG, top_p=1.1), "top_p"),
        (lambda: replace(BASE_CONFIG, max_tokens=0), "max_tokens"),
        (
            lambda: replace(BASE_CONFIG, context_fields=("revenu_mensuel", " ")),
            "blank",
        ),
        (lambda: replace(BASE_CONFIG, context_fields=("age", "age")), "duplicates"),
    ],
)
def test_agent_config_rejects_invalid_values(
    factory: Callable[[], AgentConfig], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


def test_agent_config_accepts_empty_context() -> None:
    config = replace(BASE_CONFIG, context_fields=())
    assert config.context_fields == ()
