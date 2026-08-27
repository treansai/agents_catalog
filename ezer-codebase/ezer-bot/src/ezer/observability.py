"""Structured logging and Prometheus metrics without email-content leakage."""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import re
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
_SAFE_EVENT = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,95}$")
_SAFE_MACHINE_VALUE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")

_SAFE_RECORD_FIELDS = {
    "event",
    "request_id",
    "run_id",
    "account_id_hash",
    "provider",
    "status",
    "attempt",
    "duration_ms",
    "fetched",
    "processed",
    "skipped",
    "failed",
    "dead_lettered",
    "cursor_reset",
    "model_id",
    "agent",
    "error_type",
    "error_code",
    "failure_count",
    "operation",
    "status_code",
}


class JsonFormatter(logging.Formatter):
    """Emit an allow-listed JSON record; arbitrary extras cannot leak mail content."""

    def format(self, record: logging.LogRecord) -> str:
        raw_event = getattr(record, "event", None)
        application_record = record.name == "ezer" or record.name.startswith("ezer.")
        event = (
            raw_event
            if application_record
            and isinstance(raw_event, str)
            and _SAFE_EVENT.fullmatch(raw_event) is not None
            else "external_log"
        )
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            # Third-party log messages can contain request URLs, headers, provider prose,
            # or exception values. Only application events explicitly supplied through
            # ``log_event`` are rendered; every other message gets a content-free label.
            "event": event,
        }
        request_id = (
            getattr(record, "request_id", None)
            if application_record and event != "external_log"
            else None
        ) or _request_id.get()
        if request_id:
            payload["request_id"] = request_id
        if application_record and event != "external_log":
            for key in _SAFE_RECORD_FIELDS - {"event", "request_id"}:
                value = getattr(record, key, None)
                if value is not None:
                    payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    for noisy_logger in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


@contextmanager
def request_context(request_id: str) -> Generator[None]:
    token = _request_id.set(request_id)
    try:
        yield
    finally:
        _request_id.reset(token)


def log_event(logger: logging.Logger, event: str, **fields: object) -> None:
    safe = {key: value for key, value in fields.items() if key in _SAFE_RECORD_FIELDS}
    logger.info(event, extra={"event": event, **safe})


def exception_leaves(error: BaseException) -> tuple[BaseException, ...]:
    """Flatten bounded exception groups without rendering exception messages."""

    leaves: list[BaseException] = []
    pending = [error]
    while pending and len(leaves) < 32:
        current = pending.pop()
        if isinstance(current, BaseExceptionGroup):
            pending.extend(reversed(current.exceptions))
        else:
            leaves.append(current)
    return tuple(leaves)


def safe_exception_fields(error: BaseException) -> dict[str, object]:
    """Extract allow-listed machine fields and hash account IDs from known errors."""

    fields: dict[str, object] = {"error_type": type(error).__name__}
    provider = getattr(error, "provider", None)
    if provider in {"gmail", "outlook"}:
        fields["provider"] = provider
    account_id = getattr(error, "account_id", None)
    if isinstance(account_id, str):
        fields["account_id_hash"] = hashlib.sha256(account_id.encode("utf-8")).hexdigest()[:16]
    for attribute, field_name in (("operation", "operation"), ("code", "error_code")):
        value = getattr(error, attribute, None)
        if isinstance(value, str) and _SAFE_MACHINE_VALUE.fullmatch(value):
            fields[field_name] = value
    status_code = getattr(error, "status_code", None)
    if (
        isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and 100 <= status_code <= 599
    ):
        fields["status_code"] = status_code
    return fields


class Metrics:
    """Application-local registry, which also keeps tests and app factories isolated."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.sync_total = Counter(
            "ezer_sync_total",
            "Mailbox synchronization runs",
            ("provider", "status"),
            registry=self.registry,
        )
        self.messages_total = Counter(
            "ezer_messages_total",
            "Messages handled by final processing status",
            ("provider", "status"),
            registry=self.registry,
        )
        self.sync_duration = Histogram(
            "ezer_sync_duration_seconds",
            "End-to-end mailbox sync duration",
            ("provider",),
            registry=self.registry,
            buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
        )
        self.agent_duration = Histogram(
            "ezer_agent_duration_seconds",
            "Agent invocation duration",
            ("agent", "status"),
            registry=self.registry,
            buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)


class Timer:
    def __init__(self) -> None:
        self.started = time.monotonic()

    @property
    def seconds(self) -> float:
        return time.monotonic() - self.started

    @property
    def milliseconds(self) -> int:
        return round(self.seconds * 1000)
