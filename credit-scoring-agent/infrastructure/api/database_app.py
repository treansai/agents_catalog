"""Compose the trace API with PostgreSQL-backed ports."""

from fastapi import FastAPI
from sqlalchemy import Engine

from infrastructure.api.app import UtcClock, create_app
from infrastructure.database.override_store import (
    EventIdFactory,
    make_human_override_port,
)
from infrastructure.database.run_reader import make_run_trace_lookup


def create_database_app(
    engine: Engine, utc_now: UtcClock, event_id_factory: EventIdFactory
) -> FastAPI:
    run_lookup = make_run_trace_lookup(engine)
    override = make_human_override_port(engine, event_id_factory)
    return create_app(run_lookup, override, utc_now)
