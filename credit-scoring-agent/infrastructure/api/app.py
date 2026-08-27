"""FastAPI factory with explicit trace and human-override ports."""

from collections.abc import Callable
from datetime import datetime
from functools import partial
from uuid import UUID

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from adapters.trace_serialization import (
    JsonObject,
    serialize_run_trace,
    serialize_trace_event,
)
from domain.decisions import Decision
from domain.events import HumanOverride
from domain.overrides import HumanOverridePort, OverrideCommand
from domain.run import RunTrace

type RunTraceLookup = Callable[[UUID], RunTrace | None]
type UtcClock = Callable[[], datetime]


class OverrideBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    operator_id: str = Field(min_length=1)
    corrected_decision: Decision
    reason: str = Field(min_length=1)


def create_app(
    run_lookup: RunTraceLookup,
    human_override: HumanOverridePort,
    utc_now: UtcClock,
) -> FastAPI:
    app = FastAPI(title="Agenomic credit trace API")
    _register_routes(app, run_lookup, human_override, utc_now)
    return app


def _register_routes(
    app: FastAPI, lookup: RunTraceLookup, override: HumanOverridePort, clock: UtcClock
) -> None:
    get_endpoint = partial(get_run, lookup)
    post_endpoint = partial(post_override, override, clock)
    app.add_api_route("/runs/{run_id}", get_endpoint, methods=["GET"])
    app.add_api_route(
        "/runs/{run_id}/override", post_endpoint, methods=["POST"], status_code=201
    )


def get_run(run_lookup: RunTraceLookup, run_id: UUID) -> JsonObject:
    trace = run_lookup(run_id)
    if trace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return serialize_run_trace(trace)


def post_override(
    port: HumanOverridePort,
    utc_now: UtcClock,
    run_id: UUID,
    body: OverrideBody,
) -> JsonObject:
    command = _override_command(run_id, body, utc_now())
    return serialize_trace_event(_apply_override(port, command))


def _override_command(
    run_id: UUID, body: OverrideBody, occurred_at: datetime
) -> OverrideCommand:
    return OverrideCommand(
        run_id, body.operator_id, body.corrected_decision, body.reason, occurred_at
    )


def _apply_override(port: HumanOverridePort, command: OverrideCommand) -> HumanOverride:
    try:
        return port(command)
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found") from error
