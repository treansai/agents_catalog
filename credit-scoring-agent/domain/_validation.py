from datetime import datetime, timedelta
from math import isfinite


def require_non_blank(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def require_finite_range(
    value: float, minimum: float, maximum: float, field_name: str
) -> None:
    if not isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}")


def require_utc(value: datetime) -> None:
    if value.utcoffset() != timedelta(0):
        raise ValueError("occurred_at must be timezone-aware UTC")
