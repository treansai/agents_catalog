from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from adapters.run_agent import ConfigLookup, RunPorts, RunRequest, execute_run
from domain.configuration import AgentConfig, config_hash
from domain.dataset import generate_dossiers
from domain.dossier import Dossier
from domain.events import DecisionEmitted, GuardrailOverride, ToolFailure
from domain.run import RunTrace
from domain.tools import InternalListLookup
from infrastructure.llm.scaleway import build_scaleway_llm

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures/llm/scaleway_refus.json"
FIXTURE_BODY = FIXTURE_PATH.read_text(encoding="utf-8")
NOW = datetime(2026, 8, 28, 10, tzinfo=UTC)
RUN_ID = UUID("30000000-0000-0000-0000-000000000001")
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
CONFIG_HASH = config_hash(CONFIG)
type RunResult = tuple[RunTrace, list[RunTrace]]


def _fixture_response(request: httpx.Request) -> httpx.Response:
    del request
    headers = {"Content-Type": "application/json"}
    return httpx.Response(200, content=FIXTURE_BODY.encode(), headers=headers)


def _client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_fixture_response))


def _llm_clock() -> Callable[[], int]:
    return iter((10_000_000, 22_000_000)).__next__


def _tool_clock() -> Callable[[], int]:
    ticks = (0, 1_000_000, 2_000_000, 4_000_000, 5_000_000, 8_000_000)
    return iter(ticks).__next__


def _utc_clock() -> Callable[[], datetime]:
    return iter((NOW, NOW + timedelta(seconds=1))).__next__


def _new_uuid() -> UUID:
    return RUN_ID


def _config_lookup(value: str) -> AgentConfig | None:
    return CONFIG if value == CONFIG_HASH else None


def _missing_config_lookup(value: str) -> AgentConfig | None:
    del value
    return None


def _dossier_lookup(value: str) -> Dossier | None:
    return DOSSIER if value == DOSSIER.dossier_id else None


def _internal_list_lookup(value: str) -> str | None:
    del value
    return None


def _failing_list_lookup(value: str) -> str | None:
    raise TimeoutError(f"internal list unavailable for {value}")


def _ports(
    client: httpx.Client,
    config_lookup: ConfigLookup,
    list_lookup: InternalListLookup,
    persisted: list[RunTrace],
) -> RunPorts:
    llm = build_scaleway_llm(client, "fixture-key", clock_ns=_llm_clock())
    factory = partial(RunPorts, config_lookup, _dossier_lookup, list_lookup, llm)
    return factory(persisted.append, _utc_clock(), _tool_clock(), _new_uuid)


def _execute(list_lookup: InternalListLookup = _internal_list_lookup) -> RunResult:
    persisted: list[RunTrace] = []
    with _client() as client:
        ports = _ports(client, _config_lookup, list_lookup, persisted)
        trace = execute_run(RunRequest(CONFIG_HASH, "147"), ports)
    return trace, persisted


def test_execute_run_requires_a_registered_configuration() -> None:
    persisted: list[RunTrace] = []
    with _client() as client:
        ports = _ports(client, _missing_config_lookup, _internal_list_lookup, persisted)
        with pytest.raises(LookupError, match="not registered"):
            execute_run(RunRequest(CONFIG_HASH, "147"), ports)
    assert persisted == []


def test_execute_run_records_the_three_tools_in_contract_order() -> None:
    trace, persisted = _execute()
    names = tuple(call.name for call in trace.tool_calls)
    assert names == ("fetch_dossier", "compute_debt_ratio", "check_internal_list")
    assert persisted == [trace]


def test_execute_run_preserves_rendered_prompt_and_raw_fixture_response() -> None:
    trace, _ = _execute()
    llm_call = trace.llm_calls[0]
    assert llm_call.prompt_rendered.startswith(CONFIG.prompt_template)
    assert llm_call.raw_response == FIXTURE_BODY
    assert (llm_call.tokens_in, llm_call.tokens_out, llm_call.latency_ms) == (
        184,
        47,
        12,
    )


def test_tool_failure_is_traced_without_deterministic_override() -> None:
    trace, _ = _execute(_failing_list_lookup)
    failures = tuple(event for event in trace.events if isinstance(event, ToolFailure))
    assert trace.decision == "REFUS"
    assert failures[0].tool_name == "check_internal_list"
    assert isinstance(trace.events[-1], DecisionEmitted)
    assert not any(isinstance(event, GuardrailOverride) for event in trace.events)
