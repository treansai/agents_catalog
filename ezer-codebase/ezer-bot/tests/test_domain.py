from __future__ import annotations

from datetime import UTC, datetime

from ezer.domain import EmailEnvelope


def email(**changes: object) -> EmailEnvelope:
    values: dict[str, object] = {
        "account_id": "primary",
        "provider": "gmail",
        "provider_message_id": "message-1",
        "subject": "Quarterly report",
        "sender_address": "sender@example.com",
        "received_at": datetime(2026, 8, 27, tzinfo=UTC),
        "body_text": "Please review the attached report.",
    }
    values.update(changes)
    return EmailEnvelope.model_validate(values)


def test_content_hash_is_stable_and_content_sensitive() -> None:
    first = email()
    same = email()
    changed = email(body_text="Different body")

    assert first.content_hash() == same.content_hash()
    assert first.content_hash() != changed.content_hash()
    assert len(first.content_hash()) == 64


def test_naive_received_at_is_normalized_to_utc() -> None:
    value = email(received_at=datetime(2026, 8, 27, 10, 0))

    assert value.received_at.tzinfo is UTC
