from __future__ import annotations

import json
import re

import pytest

from app.agent_ui.catalog import catalog_for_permissions, get_component_definition
from app.agent_ui.contracts import MISSING, parse_action_event, parse_data_source, parse_render_spec
from app.agent_ui.errors import AgentUiError
from app.agent_ui.schema import SchemaValidationError, assert_schema


def test_rejects_unknown_keys_and_prototype_pollution():
    polluted = json.loads('{"a":1,"__proto__":{"x":1},"constructor":{"prototype":{"p":true}}}')
    with pytest.raises(SchemaValidationError):
        assert_schema(
            polluted,
            {"type": "object", "additionalProperties": False, "properties": {"a": {"type": "number"}}},
        )


def test_requires_fallback_text_on_a_render_spec():
    with pytest.raises(AgentUiError, match="fallbackText"):
        parse_render_spec({"instanceId": "i1", "componentId": "mail.list", "componentVersion": "1.0", "props": {}})


def test_strips_workspace_fields_from_resolver_input():
    source = parse_data_source(
        {
            "mode": "resolver",
            "resolverId": "invoices.search",
            "input": {"query": "facture", "workspaceId": "evil", "account_id": "other", "limit": 20},
        }
    )
    assert source is not None
    assert source.to_json() == {
        "mode": "resolver",
        "resolverId": "invoices.search",
        "input": {"query": "facture", "limit": 20},
    }
    assert parse_data_source(MISSING) is None


def test_rejects_an_action_without_a_stable_idempotency_key():
    with pytest.raises(AgentUiError):
        parse_action_event(
            {
                "kind": "ui.action",
                "eventId": "e1",
                "messageId": "m1",
                "instanceId": "i1",
                "componentId": "mail.list",
                "componentVersion": "1.0",
                "actionId": "messages.open",
                "values": {},
                "idempotencyKey": "",
            }
        )


def test_does_not_expose_import_paths_in_the_catalog():
    catalog = catalog_for_permissions(["mail.read"])
    assert any(entry["id"] == "mail.list" for entry in catalog)
    assert re.search(r"import\(|components/|ezer-front|graph\.microsoft", json.dumps(catalog)) is None
    assert get_component_definition("not-a-component") is None


def test_catalog_matches_the_reference_dump_of_the_previous_implementation():
    from pathlib import Path

    from app.agent_ui.catalog import AGENT_UI_COMPONENTS

    reference = json.loads((Path(__file__).parent / "fixtures" / "catalog.json").read_text(encoding="utf-8"))
    assert json.loads(json.dumps(AGENT_UI_COMPONENTS)) == reference


def test_catalog_requires_the_listed_permissions():
    assert catalog_for_permissions([]) == []


def test_schema_distinguishes_booleans_from_numbers_and_enforces_integers():
    schema = {"type": "integer", "minimum": 0, "maximum": 10}
    assert_schema(3, schema)
    assert_schema(3.0, schema)
    for invalid in (True, 3.5, "3", 11, -1, None):
        with pytest.raises(SchemaValidationError):
            assert_schema(invalid, schema)
    with pytest.raises(SchemaValidationError):
        assert_schema(float("nan"), {"type": "number"})
    with pytest.raises(SchemaValidationError):
        assert_schema(1, {"type": "string", "enum": ["a"]})
    with pytest.raises(SchemaValidationError):
        assert_schema(True, {"enum": [1]})
