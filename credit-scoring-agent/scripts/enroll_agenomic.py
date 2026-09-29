"""Enroll one persisted credit-agent run with Agenomic Cloud."""

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast
from uuid import UUID

import httpx
from sqlalchemy import Engine

from domain.configuration import AgentConfig
from domain.run import RunTrace
from infrastructure.database.engine import build_engine
from infrastructure.database.run_ports import lookup_agent_config
from infrastructure.database.run_reader import load_complete_run
from infrastructure.observability.agenomic_enrollment import (
    AgenomicEnrollmentSettings,
    Milestones,
    enroll_agenomic_run,
)


@dataclass(frozen=True, slots=True)
class CliArguments:
    run_id: UUID


@dataclass(frozen=True, slots=True)
class EnrollmentSettings:
    database_url: str
    agenomic: AgenomicEnrollmentSettings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, type=UUID)
    return parser


def parse_arguments(argv: Sequence[str] | None = None) -> CliArguments:
    namespace = _parser().parse_args(argv)
    return CliArguments(cast(UUID, namespace.run_id))


def load_settings(environ: Mapping[str, str]) -> EnrollmentSettings:
    database_url = _required_environment(environ, "DATABASE_URL")
    api_url = _required_environment(environ, "AGENOMIC_API_URL")
    token = _required_environment(environ, "AGENOMIC_ENROLLMENT_TOKEN")
    return EnrollmentSettings(database_url, AgenomicEnrollmentSettings(api_url, token))


def enroll(arguments: CliArguments, settings: EnrollmentSettings) -> Milestones:
    engine = build_engine(settings.database_url)
    try:
        return _enroll_with_engine(arguments.run_id, settings.agenomic, engine)
    finally:
        engine.dispose()


def _enroll_with_engine(
    run_id: UUID, settings: AgenomicEnrollmentSettings, engine: Engine
) -> Milestones:
    trace = _required_trace(engine, run_id)
    config = _required_config(engine, trace.config_hash)
    with httpx.Client(timeout=30.0) as client:
        return enroll_agenomic_run(client, settings, config, trace)


def _required_trace(engine: Engine, run_id: UUID) -> RunTrace:
    trace = load_complete_run(engine, run_id)
    if trace is None:
        raise LookupError(f"run not found: {run_id}")
    return trace


def _required_config(engine: Engine, config_hash: str) -> AgentConfig:
    config = lookup_agent_config(engine, config_hash)
    if config is None:
        raise LookupError(f"agent config not registered: {config_hash}")
    return config


def _required_environment(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"required environment variable is missing: {name}")
    return value


def milestones_json(milestones: Milestones) -> str:
    return json.dumps(milestones, ensure_ascii=False, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    settings = load_settings(os.environ)
    print(milestones_json(enroll(arguments, settings)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
