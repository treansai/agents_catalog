import json
from collections.abc import Callable
from functools import partial
from typing import cast
from uuid import UUID

import pytest

from infrastructure.observability.agenomic_enrollment import (
    AgenomicEnrollmentSettings,
    Milestones,
)
from scripts import enroll_agenomic as enrollment_script
from scripts.enroll_agenomic import (
    CliArguments,
    EnrollmentSettings,
    milestones_json,
)

RUN_ID = UUID("70000000-0000-0000-0000-000000000147")
API_URL = "https://api.agenomic.io"
TOKEN = "fixture-enrollment-token"
MILESTONES = ["agent_detected", "trace_completed", "first_release_verified"]


def test_parse_arguments_requires_a_valid_run_identifier() -> None:
    arguments = enrollment_script.parse_arguments(["--run-id", str(RUN_ID)])
    assert arguments == CliArguments(RUN_ID)


@pytest.mark.parametrize("argv", [[], ["--run-id", "not-a-uuid"]])
def test_parse_arguments_rejects_missing_or_invalid_run_id(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        enrollment_script.parse_arguments(argv)


def test_load_settings_separates_enrollment_credentials() -> None:
    environ = _valid_environment()
    settings = enrollment_script.load_settings(environ)
    assert settings.database_url == environ["DATABASE_URL"]
    assert settings.agenomic.api_url == API_URL
    assert settings.agenomic.enrollment_token == TOKEN


@pytest.mark.parametrize(
    "missing", ["DATABASE_URL", "AGENOMIC_API_URL", "AGENOMIC_ENROLLMENT_TOKEN"]
)
def test_load_settings_rejects_missing_or_blank_values(missing: str) -> None:
    environ = _valid_environment()
    environ[missing] = " "
    with pytest.raises(RuntimeError, match=missing):
        enrollment_script.load_settings(environ)


def _fake_enroll(
    captured: list[tuple[CliArguments, EnrollmentSettings]],
    arguments: CliArguments,
    settings: EnrollmentSettings,
) -> Milestones:
    captured.append((arguments, settings))
    return MILESTONES


def test_main_enrolls_selected_run_and_prints_milestones(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: list[tuple[CliArguments, EnrollmentSettings]] = []
    _patch_runtime(monkeypatch, captured)
    exit_code = enrollment_script.main(["--run-id", str(RUN_ID)])
    assert exit_code == 0
    assert captured == [(CliArguments(RUN_ID), _expected_settings())]
    assert json.loads(capsys.readouterr().out) == MILESTONES


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    captured: list[tuple[CliArguments, EnrollmentSettings]],
) -> None:
    fake: Callable[[CliArguments, EnrollmentSettings], Milestones]
    fake = partial(_fake_enroll, captured)
    monkeypatch.setattr(enrollment_script, "enroll", fake)
    for name, value in _valid_environment().items():
        monkeypatch.setenv(name, value)


def _valid_environment() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql+psycopg://db/test",
        "AGENOMIC_API_URL": API_URL,
        "AGENOMIC_ENROLLMENT_TOKEN": TOKEN,
    }


def _expected_settings() -> EnrollmentSettings:
    agenomic = AgenomicEnrollmentSettings(API_URL, TOKEN)
    return EnrollmentSettings("postgresql+psycopg://db/test", agenomic)


def test_milestones_json_is_stable_utf8_json() -> None:
    rendered = milestones_json(["agent_detected", "vérifié"])
    assert cast(list[str], json.loads(rendered)) == ["agent_detected", "vérifié"]
    assert "\\u00e9" not in rendered
