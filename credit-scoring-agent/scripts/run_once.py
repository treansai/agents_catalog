"""Execute one registered agent configuration against one stored dossier."""

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from time import perf_counter_ns
from typing import cast
from uuid import uuid4

import httpx
from sqlalchemy import Engine

from adapters.run_agent import (
    ClockNs,
    ConfigLookup,
    RunPorts,
    RunRequest,
    UtcClock,
    UuidFactory,
    execute_run,
)
from domain.llm import LlmPort
from domain.run import PersistRunPort, RunTrace
from domain.tools import DossierLookup, InternalListLookup
from infrastructure.database.engine import build_engine
from infrastructure.database.run_ports import (
    lookup_agent_config,
    lookup_dossier,
    lookup_internal_reason,
)
from infrastructure.database.run_store import make_persist_run_port
from infrastructure.llm.scaleway import build_scaleway_llm

type LookupPorts = tuple[ConfigLookup, DossierLookup, InternalListLookup]
type RuntimePorts = tuple[LlmPort, PersistRunPort, UtcClock, ClockNs, UuidFactory]
type RunPortValues = tuple[
    ConfigLookup, DossierLookup, InternalListLookup, *RuntimePorts
]
type SummaryValue = str | float | int


@dataclass(frozen=True, slots=True)
class CliArguments:
    config_hash: str
    dossier_id: str


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    database_url: str
    scaleway_api_key: str


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-hash", required=True)
    parser.add_argument("--dossier-id", required=True)
    return parser


def parse_arguments(argv: Sequence[str] | None = None) -> CliArguments:
    namespace = _parser().parse_args(argv)
    config_hash = cast(str, namespace.config_hash)
    dossier_id = cast(str, namespace.dossier_id)
    return CliArguments(config_hash, dossier_id)


def load_settings(environ: Mapping[str, str]) -> RuntimeSettings:
    database_url = _required_environment(environ, "DATABASE_URL")
    api_key = _required_environment(environ, "SCW_SECRET_KEY")
    return RuntimeSettings(database_url, api_key)


def _required_environment(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"required environment variable is missing: {name}")
    return value


def run_once(arguments: CliArguments, settings: RuntimeSettings) -> RunTrace:
    engine = build_engine(settings.database_url)
    try:
        with httpx.Client(timeout=60.0) as client:
            return _execute(arguments, settings.scaleway_api_key, engine, client)
    finally:
        engine.dispose()


def _execute(
    arguments: CliArguments, api_key: str, engine: Engine, client: httpx.Client
) -> RunTrace:
    request = RunRequest(arguments.config_hash, arguments.dossier_id)
    ports = _run_ports(engine, client, api_key)
    return execute_run(request, ports)


def _run_ports(engine: Engine, client: httpx.Client, api_key: str) -> RunPorts:
    lookups = _lookup_ports(engine)
    runtime = _runtime_ports(engine, client, api_key)
    values: RunPortValues = (*lookups, *runtime)
    return RunPorts(*values)


def _lookup_ports(engine: Engine) -> LookupPorts:
    return (
        partial(lookup_agent_config, engine),
        partial(lookup_dossier, engine),
        partial(lookup_internal_reason, engine),
    )


def _runtime_ports(engine: Engine, client: httpx.Client, api_key: str) -> RuntimePorts:
    llm = build_scaleway_llm(client, api_key)
    persist_run = make_persist_run_port(engine)
    return llm, persist_run, _utc_now, perf_counter_ns, uuid4


def _utc_now() -> datetime:
    return datetime.now(UTC)


def summary_payload(trace: RunTrace) -> dict[str, SummaryValue]:
    return {
        **_identity_summary(trace),
        "started_at": trace.started_at.isoformat(),
        "ended_at": trace.ended_at.isoformat(),
        "decision": trace.decision,
        "justification": trace.justification,
        "confidence": trace.confidence,
    }


def _identity_summary(trace: RunTrace) -> dict[str, SummaryValue]:
    return {
        "run_id": str(trace.run_id),
        "config_hash": trace.config_hash,
        "dossier_id": trace.dossier_id,
    }


def summary_json(trace: RunTrace) -> str:
    return json.dumps(summary_payload(trace), ensure_ascii=False, sort_keys=True)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    settings = load_settings(os.environ)
    trace = run_once(arguments, settings)
    print(summary_json(trace))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
