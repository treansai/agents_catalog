"""Functional adapters for deterministic agent tools and their serialization."""

import json
from collections.abc import Sequence

from domain.dataset import dossier_to_payload as _dataset_dossier_payload
from domain.dossier import DebtRatio, Dossier
from domain.scoring import compute_debt_ratio as _compute_debt_ratio
from domain.tools import (
    DossierLookup,
    InternalListLookup,
    JsonObject,
    JsonValue,
    ListStatus,
    ToolCallTrace,
)


def fetch_dossier(dossier_id: str, lookup: DossierLookup) -> Dossier:
    dossier = lookup(dossier_id)
    if dossier is None:
        raise LookupError(f"dossier not found: {dossier_id}")
    if dossier.dossier_id != dossier_id:
        raise ValueError("dossier lookup returned a mismatched identifier")
    return dossier


def compute_debt_ratio(dossier: Dossier) -> DebtRatio:
    return _compute_debt_ratio(dossier)


def check_internal_list(dossier: Dossier, lookup: InternalListLookup) -> ListStatus:
    reason = lookup(dossier.dossier_id)
    return ListStatus(dossier.dossier_id, reason is not None, reason)


def dossier_to_payload(dossier: Dossier) -> dict[str, str | int]:
    return _dataset_dossier_payload(dossier)


def debt_ratio_to_payload(ratio: DebtRatio) -> dict[str, JsonValue]:
    return {
        "taux_endettement": format(ratio.taux_endettement, "f"),
        "reste_a_vivre": format(ratio.reste_a_vivre, "f"),
    }


def list_status_to_payload(status: ListStatus) -> dict[str, JsonValue]:
    return {
        "dossier_id": status.dossier_id,
        "is_listed": status.is_listed,
        "reason": status.reason,
    }


def _require_known_fields(fields: Sequence[str], payload: JsonObject) -> None:
    unknown = set(fields).difference(payload)
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"unknown dossier context fields: {names}")


def dossier_context_payload(
    dossier: Dossier, context_fields: Sequence[str]
) -> dict[str, JsonValue]:
    payload = dossier_to_payload(dossier)
    _require_known_fields(context_fields, payload)
    return {field: payload[field] for field in context_fields}


def tool_call_trace_to_payload(trace: ToolCallTrace) -> dict[str, JsonValue]:
    output = None if trace.output is None else dict(trace.output)
    return {
        "name": trace.name,
        "input": dict(trace.input),
        "output": output,
        "duration_ms": trace.duration_ms,
        "error": trace.error,
    }


def canonical_tool_json(payload: JsonObject) -> str:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
