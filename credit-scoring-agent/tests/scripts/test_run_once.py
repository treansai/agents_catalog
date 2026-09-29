import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from adapters.run_agent import RunPorts, RunRequest
from domain.events import DecisionEmitted
from domain.run import LlmCallTrace, RunTrace
from domain.tools import ToolCallTrace
from infrastructure.observability.agenomic import AgenomicSettings, AgenomicTelemetry
from scripts import run_once as run_once_script
from scripts.run_once import CliArguments, RuntimeSettings

RUN_ID = UUID("10000000-0000-0000-0000-000000000147")
CONFIG_HASH = "a" * 64
AGENOMIC_URL = "https://api.agenomic.io"
AGENOMIC_API_KEY = "fixture-write-api-key"
STARTED_AT = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)
ENDED_AT = STARTED_AT + timedelta(milliseconds=25)
EVENT = DecisionEmitted(
    RUN_ID, "REFUS", "Ratio 48.75 %, reste 1230.00 EUR.", 0.87, ENDED_AT
)
TOOLS = (
    ToolCallTrace("fetch_dossier", {}, {}, 1, None),
    ToolCallTrace("compute_debt_ratio", {}, {}, 1, None),
    ToolCallTrace("check_internal_list", {}, {}, 1, None),
)
LLM_CALLS = (LlmCallTrace("prompt", '{"decision":"REFUS"}', 1, 1, 1),)
TRACE = RunTrace(
    RUN_ID,
    CONFIG_HASH,
    "147",
    STARTED_AT,
    ENDED_AT,
    TOOLS,
    LLM_CALLS,
    "REFUS",
    EVENT.justification,
    EVENT.confidence,
    (EVENT,),
)


def test_parse_arguments_requires_both_identifiers() -> None:
    arguments = run_once_script.parse_arguments(
        ["--config-hash", CONFIG_HASH, "--dossier-id", "147"]
    )
    assert arguments == CliArguments(CONFIG_HASH, "147")


@pytest.mark.parametrize(
    "argv",
    [[], ["--config-hash", CONFIG_HASH], ["--dossier-id", "147"]],
)
def test_parse_arguments_rejects_missing_identifiers(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        run_once_script.parse_arguments(argv)


def test_load_settings_reads_only_required_environment() -> None:
    environ = _valid_environment()
    agenomic = AgenomicSettings(AGENOMIC_URL, AGENOMIC_API_KEY)
    expected = RuntimeSettings(
        environ["DATABASE_URL"], environ["SCW_SECRET_KEY"], agenomic
    )
    assert run_once_script.load_settings(environ) == expected


@pytest.mark.parametrize(
    "missing",
    ["DATABASE_URL", "SCW_SECRET_KEY"],
)
def test_load_settings_rejects_missing_or_blank_values(missing: str) -> None:
    environ = _valid_environment()
    environ[missing] = " "
    with pytest.raises(RuntimeError, match=missing):
        run_once_script.load_settings(environ)


def test_load_settings_allows_bootstrap_without_agenomic_api_key() -> None:
    environ = _valid_environment()
    del environ["AGENOMIC_API_KEY"]
    expected = RuntimeSettings(environ["DATABASE_URL"], environ["SCW_SECRET_KEY"], None)
    assert run_once_script.load_settings(environ) == expected


def test_load_settings_requires_api_url_when_export_is_enabled() -> None:
    environ = _valid_environment()
    environ["AGENOMIC_API_URL"] = " "
    with pytest.raises(RuntimeError, match="AGENOMIC_API_URL"):
        run_once_script.load_settings(environ)


def _plain_run(request: RunRequest, ports: RunPorts) -> RunTrace:
    del request, ports
    return TRACE


def _observed_run(
    request: RunRequest, ports: RunPorts, telemetry: AgenomicTelemetry
) -> RunTrace:
    del request, ports, telemetry
    return TRACE


def test_invoke_uses_plain_graph_during_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_once_script, "execute_run", _plain_run)
    request = RunRequest(CONFIG_HASH, "147")
    assert run_once_script._invoke(request, _ports_stub(), None) == TRACE


def test_invoke_uses_agenomic_graph_when_export_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_once_script, "execute_agenomic_run", _observed_run)
    request = RunRequest(CONFIG_HASH, "147")
    telemetry = cast(AgenomicTelemetry, object())
    assert run_once_script._invoke(request, _ports_stub(), telemetry) == TRACE


def _ports_stub() -> RunPorts:
    return cast(RunPorts, object())


def _fake_run(
    captured: list[tuple[CliArguments, RuntimeSettings]],
    arguments: CliArguments,
    settings: RuntimeSettings,
) -> RunTrace:
    captured.append((arguments, settings))
    return TRACE


def test_main_composes_boundary_without_live_services(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: list[tuple[CliArguments, RuntimeSettings]] = []
    _patch_runtime(monkeypatch, captured)
    exit_code = run_once_script.main(_valid_argv())
    assert exit_code == 0
    assert captured == [(CliArguments(CONFIG_HASH, "147"), _expected_settings())]
    assert _captured_json(capsys) == run_once_script.summary_payload(TRACE)


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    captured: list[tuple[CliArguments, RuntimeSettings]],
) -> None:
    fake: Callable[[CliArguments, RuntimeSettings], RunTrace]
    fake = partial(_fake_run, captured)
    monkeypatch.setattr(run_once_script, "run_once", fake)
    for name, value in _valid_environment().items():
        monkeypatch.setenv(name, value)


def _valid_environment() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql+psycopg://db/test",
        "SCW_SECRET_KEY": "fixture-secret",
        "AGENOMIC_API_URL": AGENOMIC_URL,
        "AGENOMIC_API_KEY": AGENOMIC_API_KEY,
    }


def _valid_argv() -> list[str]:
    return ["--config-hash", CONFIG_HASH, "--dossier-id", "147"]


def _expected_settings() -> RuntimeSettings:
    agenomic = AgenomicSettings(AGENOMIC_URL, AGENOMIC_API_KEY)
    return RuntimeSettings("postgresql+psycopg://db/test", "fixture-secret", agenomic)


def _captured_json(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    return cast(dict[str, object], json.loads(capsys.readouterr().out))


def test_summary_json_is_audit_friendly_and_does_not_include_secrets() -> None:
    payload = cast(dict[str, object], json.loads(run_once_script.summary_json(TRACE)))
    assert payload["run_id"] == str(RUN_ID)
    assert payload["config_hash"] == CONFIG_HASH
    assert payload["dossier_id"] == "147"
    assert payload["decision"] == "REFUS"
    assert payload["started_at"] == STARTED_AT.isoformat()
    assert payload["ended_at"] == ENDED_AT.isoformat()
    assert "secret" not in payload


def test_env_example_documents_agenomic_cloud_credentials() -> None:
    example = Path(".env.example").read_text(encoding="utf-8")
    assert 'export AGENOMIC_API_URL="https://api.agenomic.io"' in example
    assert 'export AGENOMIC_ENROLLMENT_TOKEN="<your-enrollment-token>"' in example
    assert 'export AGENOMIC_API_KEY="<your-write-api-key>"' in example
    assert AGENOMIC_API_KEY not in example
