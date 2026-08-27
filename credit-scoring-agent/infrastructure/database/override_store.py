"""Transactional persistence for first-class human override events."""

from collections.abc import Callable
from functools import partial
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Connection, Engine

from domain.decisions import Decision, parse_decision
from domain.events import HumanOverride, TraceEvent
from domain.overrides import (
    HumanOverridePort,
    OverrideCommand,
    build_human_override,
    effective_decision,
)
from infrastructure.database.repositories import insert_trace_event, list_trace_events
from infrastructure.database.schema import run_trace_table, trace_event_table

type EventIdFactory = Callable[[], UUID]


def make_human_override_port(
    engine: Engine, event_id_factory: EventIdFactory
) -> HumanOverridePort:
    return partial(append_human_override, engine, event_id_factory)


def append_human_override(
    engine: Engine, event_id_factory: EventIdFactory, command: OverrideCommand
) -> HumanOverride:
    with engine.begin() as connection:
        return _append_locked(connection, event_id_factory, command)


def _append_locked(
    connection: Connection, event_id_factory: EventIdFactory, command: OverrideCommand
) -> HumanOverride:
    initial = _locked_initial_decision(connection, command.run_id)
    events = _ordered_events(connection, command.run_id)
    original = effective_decision(initial, events)
    override = build_human_override(command, original)
    sequence_no = _next_sequence_no(connection, command.run_id)
    insert_trace_event(connection, event_id_factory(), override, sequence_no)
    return override


def _locked_initial_decision(connection: Connection, run_id: UUID) -> Decision:
    query = sa.select(run_trace_table.c.decision).where(
        run_trace_table.c.run_id == run_id
    )
    value = connection.scalar(query.with_for_update())
    if value is None:
        raise LookupError(f"run not found: {run_id}")
    return parse_decision(cast(str, value))


def _ordered_events(connection: Connection, run_id: UUID) -> tuple[TraceEvent, ...]:
    return tuple(item.event for item in list_trace_events(connection, run_id))


def _next_sequence_no(connection: Connection, run_id: UUID) -> int:
    maximum = sa.func.max(trace_event_table.c.sequence_no)
    query = sa.select(maximum).where(trace_event_table.c.run_id == run_id)
    current = connection.scalar(query)
    return 0 if current is None else int(current) + 1
