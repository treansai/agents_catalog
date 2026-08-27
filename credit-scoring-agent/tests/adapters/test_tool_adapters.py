import json
from decimal import Decimal

import pytest

from adapters.tools import (
    canonical_tool_json,
    check_internal_list,
    compute_debt_ratio,
    debt_ratio_to_payload,
    dossier_context_payload,
    dossier_to_payload,
    fetch_dossier,
    list_status_to_payload,
    tool_call_trace_to_payload,
)
from domain.dossier import DebtRatio, Dossier
from domain.tools import JsonValue, ListStatus, ToolCallTrace

DOSSIER = Dossier(
    "147",
    Decimal("3200.00"),
    Decimal("900.00"),
    Decimal("12000.00"),
    48,
    72,
    "CDI",
    38,
    "75011",
    1,
)


def _dossier_lookup(dossier_id: str) -> Dossier | None:
    return DOSSIER if dossier_id == DOSSIER.dossier_id else None


def _internal_list_lookup(dossier_id: str) -> str | None:
    return "Incident synthétique" if dossier_id == DOSSIER.dossier_id else None


def _empty_internal_list_lookup(dossier_id: str) -> str | None:
    del dossier_id
    return None


def test_fetch_dossier_uses_injected_lookup() -> None:
    assert fetch_dossier("147", _dossier_lookup) == DOSSIER


def test_fetch_dossier_raises_lookup_error_when_absent() -> None:
    with pytest.raises(LookupError, match="missing"):
        fetch_dossier("missing", _dossier_lookup)


def test_fetch_dossier_rejects_a_mismatched_identifier() -> None:
    with pytest.raises(ValueError, match="mismatched"):
        fetch_dossier("148", lambda _dossier_id: DOSSIER)


def test_compute_debt_ratio_tool_delegates_to_pure_domain_calculation() -> None:
    assert compute_debt_ratio(DOSSIER) == DebtRatio(
        Decimal("28.13"), Decimal("2300.00")
    )


def test_check_internal_list_maps_present_and_absent_entries() -> None:
    listed = check_internal_list(DOSSIER, _internal_list_lookup)
    clear = check_internal_list(DOSSIER, _empty_internal_list_lookup)
    assert listed == ListStatus("147", True, "Incident synthétique")
    assert clear == ListStatus("147", False, None)


def test_tool_outputs_use_exact_decimal_strings() -> None:
    dossier = dossier_to_payload(DOSSIER)
    ratio = debt_ratio_to_payload(compute_debt_ratio(DOSSIER))
    assert dossier["revenu_mensuel"] == "3200.00"
    assert ratio == {"taux_endettement": "28.13", "reste_a_vivre": "2300.00"}


def test_list_status_serialization_preserves_reason() -> None:
    status = list_status_to_payload(ListStatus("147", True, "Incident synthétique"))
    assert status == {
        "dossier_id": "147",
        "is_listed": True,
        "reason": "Incident synthétique",
    }


def test_dossier_context_selects_only_configured_fields() -> None:
    context = dossier_context_payload(DOSSIER, ("age", "revenu_mensuel"))
    assert context == {"age": 38, "revenu_mensuel": "3200.00"}


def test_dossier_context_rejects_unknown_fields_deterministically() -> None:
    with pytest.raises(ValueError, match="bad_a, bad_z"):
        dossier_context_payload(DOSSIER, ("bad_z", "bad_a"))


def test_trace_serialization_preserves_success_and_failure_shape() -> None:
    success = ToolCallTrace(
        "fetch_dossier", {"dossier_id": "147"}, {"age": 38}, 4, None
    )
    failure = ToolCallTrace("fetch_dossier", {"dossier_id": "404"}, None, 9, "absent")
    assert tool_call_trace_to_payload(success)["output"] == {"age": 38}
    assert tool_call_trace_to_payload(failure)["output"] is None
    assert tool_call_trace_to_payload(failure)["error"] == "absent"


def test_canonical_tool_json_is_compact_sorted_utf8_and_order_independent() -> None:
    first: dict[str, JsonValue] = {"résultat": "éligible", "a": 1}
    second = dict(reversed(tuple(first.items())))
    expected = '{"a":1,"résultat":"éligible"}'
    assert canonical_tool_json(first) == expected
    assert canonical_tool_json(first) == canonical_tool_json(second)
    assert json.loads(expected) == first


def test_canonical_tool_json_rejects_non_finite_numbers() -> None:
    with pytest.raises(ValueError, match="JSON compliant"):
        canonical_tool_json({"latency": float("nan")})
