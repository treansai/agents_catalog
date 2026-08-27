from dataclasses import replace

from adapters.prompting import EVIDENCE_HEADER, render_prompt
from adapters.tools import (
    compute_debt_ratio,
    debt_ratio_to_payload,
    dossier_to_payload,
    list_status_to_payload,
)
from domain.configuration import AgentConfig
from domain.dataset import generate_dossiers
from domain.tools import ListStatus, ToolCallTrace

DOSSIER = generate_dossiers()[146]
CONFIG = AgentConfig(
    "Analyse ce dossier synthétique.",
    "mistral-nemo-instruct-2407",
    0.0,
    1.0,
    512,
    "1.0.0",
    ("revenu_mensuel", "charges_mensuelles", "nb_incidents_passes"),
)


def _fetch_call() -> ToolCallTrace:
    return ToolCallTrace(
        "fetch_dossier", {"dossier_id": "147"}, dossier_to_payload(DOSSIER), 1, None
    )


def _ratio_call() -> ToolCallTrace:
    ratio = debt_ratio_to_payload(compute_debt_ratio(DOSSIER))
    return ToolCallTrace(
        "compute_debt_ratio", dossier_to_payload(DOSSIER), ratio, 2, None
    )


def _list_call() -> ToolCallTrace:
    status = ListStatus("147", False, None)
    output = list_status_to_payload(status)
    return ToolCallTrace(
        "check_internal_list", dossier_to_payload(DOSSIER), output, 3, None
    )


def _calls() -> tuple[ToolCallTrace, ...]:
    return _fetch_call(), _ratio_call(), _list_call()


def test_render_prompt_is_deterministic_and_keeps_all_tool_evidence() -> None:
    first = render_prompt(CONFIG, DOSSIER, _calls())
    second = render_prompt(CONFIG, DOSSIER, _calls())
    assert first == second
    assert first.startswith(f"{CONFIG.prompt_template}\n\n{EVIDENCE_HEADER}:\n")
    assert first.count('"name":') == 3


def test_prompt_omits_postcode_when_context_does_not_allow_it() -> None:
    prompt = render_prompt(CONFIG, DOSSIER, _calls())
    assert '"code_postal"' not in prompt
    assert DOSSIER.code_postal not in prompt


def test_prompt_includes_postcode_only_when_context_allows_it() -> None:
    fields = (*CONFIG.context_fields, "code_postal")
    config = replace(CONFIG, context_fields=fields)
    prompt = render_prompt(config, DOSSIER, _calls())
    assert f'"code_postal":"{DOSSIER.code_postal}"' in prompt
