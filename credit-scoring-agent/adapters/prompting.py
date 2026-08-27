"""Render the exact prompt from a versioned config and tool evidence."""

from adapters.tools import (
    canonical_tool_json,
    dossier_context_payload,
)
from domain.configuration import AgentConfig
from domain.dossier import Dossier
from domain.tools import JsonObject, JsonValue, ToolCallTrace

EVIDENCE_HEADER = "DONNEES_ET_SORTIES_OUTILS_JSON"


def render_prompt(
    config: AgentConfig, dossier: Dossier, calls: tuple[ToolCallTrace, ...]
) -> str:
    evidence = _prompt_evidence(config, dossier, calls)
    evidence_json = canonical_tool_json(evidence)
    return f"{config.prompt_template.rstrip()}\n\n{EVIDENCE_HEADER}:\n{evidence_json}"


def _prompt_evidence(
    config: AgentConfig, dossier: Dossier, calls: tuple[ToolCallTrace, ...]
) -> JsonObject:
    context = dossier_context_payload(dossier, config.context_fields)
    rendered_calls: list[JsonValue] = [_prompt_call(call, context) for call in calls]
    return {"dossier_context": context, "tool_calls": rendered_calls}


def _prompt_call(call: ToolCallTrace, context: JsonObject) -> JsonObject:
    output: JsonValue = call.output
    if call.name == "fetch_dossier" and call.output is not None:
        output = context
    return {"name": call.name, "output": output, "error": call.error}
