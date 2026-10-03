"""Horodatages au format ISO 8601 de JavaScript (`Date.prototype.toISOString`)."""

from __future__ import annotations

from datetime import UTC, datetime


def parse_instant(value: str) -> datetime | None:
    """Analyse un instant ISO 8601 ; renvoie None s'il est invalide (équivaut à `Date.parse` → NaN)."""
    text = value.strip()
    if text == "":
        return None
    if text[-1] in "zZ":
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def format_instant(moment: datetime) -> str:
    utc = moment.astimezone(UTC)
    return f"{utc:%Y-%m-%dT%H:%M:%S}.{utc.microsecond // 1000:03d}Z"


def to_iso(value: str) -> str:
    """`new Date(value).toISOString()` ; lève ValueError si la date est invalide."""
    parsed = parse_instant(value)
    if parsed is None:
        raise ValueError("invalid time value")
    return format_instant(parsed)


def now_iso() -> str:
    return format_instant(datetime.now(UTC))
