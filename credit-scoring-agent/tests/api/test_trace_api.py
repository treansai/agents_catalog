from datetime import UTC, datetime, timedelta
from functools import partial
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from domain.events import DecisionEmitted, HumanOverride
from domain.overrides import OverrideCommand
from domain.run import LlmCallTrace, RunTrace
from domain.tools import ToolCallTrace
from infrastructure.api.app import create_app

RUN_ID = UUID("10000000-0000-0000-0000-000000000147")
MISSING_RUN_ID = UUID("20000000-0000-0000-0000-000000000000")
NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
ENDED_AT = NOW + timedelta(milliseconds=30)
JUSTIFICATION = "Taux 48.75 %, reste à vivre 1230.00 EUR, 2 incidents."
TOOLS = (
    ToolCallTrace("fetch_dossier", {"dossier_id": "147"}, {"age": 38}, 2, None),
    ToolCallTrace("compute_debt_ratio", {}, {"taux_endettement": "48.75"}, 1, None),
    ToolCallTrace("check_internal_list", {}, {"is_listed": True}, 1, None),
)
LLMS = (LlmCallTrace("prompt rendu", '{"decision":"REFUS"}', 184, 47, 12),)
TOOL_NAMES = [call.name for call in TOOLS]
EMITTED = DecisionEmitted(RUN_ID, "REFUS", JUSTIFICATION, 0.87, ENDED_AT)
TRACE = RunTrace(
    RUN_ID,
    "a" * 64,
    "147",
    NOW,
    ENDED_AT,
    TOOLS,
    LLMS,
    "REFUS",
    JUSTIFICATION,
    0.87,
    (EMITTED,),
)


def _lookup(run_id: UUID) -> RunTrace | None:
    return TRACE if run_id == RUN_ID else None


def _store_override(
    captured: list[OverrideCommand], command: OverrideCommand
) -> HumanOverride:
    captured.append(command)
    return _override_event(command)


def _override_event(command: OverrideCommand) -> HumanOverride:
    return HumanOverride(
        command.run_id,
        command.operator_id,
        "REFUS",
        command.corrected_decision,
        command.reason,
        command.occurred_at,
    )


def _client(captured: list[OverrideCommand]) -> TestClient:
    port = partial(_store_override, captured)
    return TestClient(create_app(_lookup, port, lambda: NOW))


def _override_payload() -> dict[str, str]:
    return {
        "operator_id": "analyst-7",
        "corrected_decision": "ACCORD",
        "reason": "Pièces justificatives vérifiées.",
    }


def _post(client: TestClient, run_id: UUID, payload: dict[str, str]) -> Response:
    return client.post(f"/runs/{run_id}/override", json=payload)


def test_get_run_returns_complete_serialized_trace() -> None:
    response = _client([]).get(f"/runs/{RUN_ID}")
    payload = response.json()
    assert response.status_code == 200
    assert payload["run_id"] == str(RUN_ID)
    assert payload["config_hash"] == TRACE.config_hash
    assert [call["name"] for call in payload["tool_calls"]] == TOOL_NAMES
    assert payload["llm_calls"][0]["prompt_rendered"] == "prompt rendu"
    assert payload["events"][0]["event_type"] == "DECISION_EMITTED"


def test_get_unknown_run_returns_404() -> None:
    response = _client([]).get(f"/runs/{MISSING_RUN_ID}")
    assert response.status_code == 404
    assert response.json() == {"detail": "run not found"}


def test_post_override_creates_auditable_event_with_injected_clock() -> None:
    captured: list[OverrideCommand] = []
    response = _post(_client(captured), RUN_ID, _override_payload())
    expected = OverrideCommand(
        RUN_ID, "analyst-7", "ACCORD", "Pièces justificatives vérifiées.", NOW
    )
    assert response.status_code == 201
    assert captured == [expected]
    assert response.json() == _expected_override_payload()


def _expected_override_payload() -> dict[str, object]:
    return {
        "run_id": str(RUN_ID),
        "operator_id": "analyst-7",
        "original_decision": "REFUS",
        "corrected_decision": "ACCORD",
        "reason": "Pièces justificatives vérifiées.",
        "occurred_at": NOW.isoformat(),
        "event_type": "HUMAN_OVERRIDE",
    }


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_invalid_reason_returns_422_before_override_port(reason: str | None) -> None:
    captured: list[OverrideCommand] = []
    payload = _override_payload()
    if reason is None:
        payload.pop("reason")
    else:
        payload["reason"] = reason
    response = _post(_client(captured), RUN_ID, payload)
    assert response.status_code == 422
    assert captured == []


def test_client_cannot_supply_original_decision() -> None:
    captured: list[OverrideCommand] = []
    payload = {**_override_payload(), "original_decision": "ACCORD"}
    response = _post(_client(captured), RUN_ID, payload)
    assert response.status_code == 422
    assert captured == []


def _missing_override(command: OverrideCommand) -> HumanOverride:
    raise LookupError(command.run_id)


def test_post_override_maps_missing_run_to_404() -> None:
    app = create_app(_lookup, _missing_override, lambda: NOW)
    response = _post(TestClient(app), MISSING_RUN_ID, _override_payload())
    assert response.status_code == 404
    assert response.json() == {"detail": "run not found"}


@pytest.mark.parametrize("corrected", ["REVIEW", "", "refus"])
def test_post_override_rejects_unknown_decision(corrected: str) -> None:
    captured: list[OverrideCommand] = []
    payload = _override_payload()
    payload["corrected_decision"] = corrected
    response = _post(_client(captured), RUN_ID, payload)
    assert response.status_code == 422
    assert captured == []
