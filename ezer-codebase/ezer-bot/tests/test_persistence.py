from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from ezer.config import GmailAccount, StaticTokenAuth
from ezer.domain import EmailAnalysis, EmailEnvelope, SafetyAssessment
from ezer.persistence import (
    LeaseLostError,
    SQLitePersistence,
    analysis_id_for,
)

NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SQLitePersistence]:
    path = tmp_path / "ezer.db"
    persistence = SQLitePersistence(path)
    await persistence.open()
    try:
        yield persistence
    finally:
        await persistence.close()


@pytest.fixture
def account() -> GmailAccount:
    return GmailAccount(
        provider="gmail",
        id="primary",
        auth=StaticTokenAuth(
            type="access_token",
            access_token=SecretStr("test-token"),
        ),
    )


@pytest.fixture
def message() -> EmailEnvelope:
    return EmailEnvelope(
        account_id="primary",
        provider="gmail",
        provider_message_id="message-1",
        subject="Status",
        received_at=NOW,
        body_text="A bounded test message.",
    )


def analysis_for(claim_id: str, message: EmailEnvelope) -> EmailAnalysis:
    return EmailAnalysis(
        analysis_id=claim_id,
        message_ref=message.message_ref(),
        content_hash=message.content_hash(),
        pipeline_version="pipeline-v1",
        model_id="fake-sonnet-5",
        prompt_version="fake-prompts-v1",
        created_at=NOW,
        category="informational",
        priority="normal",
        needs_human_review=False,
        summary="Résumé de test.",
        safety=SafetyAssessment(
            risk_level="none",
            phishing_likelihood=0,
            rationale="No security concern.",
            confidence=1,
        ),
    )


async def test_cursor_compare_and_set_including_reset_to_none(
    store: SQLitePersistence,
) -> None:
    assert await store.get_cursor("primary") is None
    assert await store.set_cursor("primary", None, "cursor-1") is True
    assert await store.set_cursor("primary", None, "stale-bootstrap") is False
    assert await store.set_cursor("primary", "cursor-1", "cursor-2") is True
    assert await store.set_cursor("primary", "cursor-1", "stale-page") is False
    assert await store.get_cursor("primary") == "cursor-2"

    assert await store.set_cursor("primary", "cursor-2", None) is True
    assert await store.get_cursor("primary") is None
    assert await store.set_cursor("primary", "cursor-2", "regression") is False
    assert await store.set_cursor("primary", None, "fresh-bootstrap") is True
    assert await store.get_cursor("primary") == "fresh-bootstrap"


async def test_claim_is_idempotent_reclaims_expired_lease_and_dead_letters(
    store: SQLitePersistence,
    account: GmailAccount,
    message: EmailEnvelope,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [1_800_000_000.0]
    monkeypatch.setattr("ezer.persistence.time.time", lambda: clock[0])
    content_hash = message.content_hash()

    first = await store.claim(account, message, content_hash, "pipeline-v1", 30, 2)
    assert first is not None
    assert first.analysis_id == analysis_id_for(
        account.id,
        message.provider,
        message.provider_message_id,
        content_hash,
        "pipeline-v1",
    )
    assert first.attempt == 1
    assert await store.claim(account, message, content_hash, "pipeline-v1", 30, 2) is None

    clock[0] += 31
    second = await store.claim(account, message, content_hash, "pipeline-v1", 30, 2)
    assert second is not None
    assert second.analysis_id == first.analysis_id
    assert second.attempt == 2
    assert second.lease_token != first.lease_token

    with pytest.raises(LeaseLostError, match="lease"):
        await store.complete(first, analysis_for(first.analysis_id, message))

    assert await store.fail(second, RuntimeError("private provider prose"), 2) == "dead_letter"
    assert await store.get_status(second.analysis_id) == "dead_letter"
    assert await store.get_analysis(second.analysis_id) is None
    assert await store.claim(account, message, content_hash, "pipeline-v1", 30, 2) is None

    with pytest.raises(LeaseLostError, match="lease"):
        await store.complete(second, analysis_for(second.analysis_id, message))


async def test_complete_rejects_analysis_that_does_not_match_claim(
    store: SQLitePersistence,
    account: GmailAccount,
    message: EmailEnvelope,
) -> None:
    claim = await store.claim(
        account,
        message,
        message.content_hash(),
        "pipeline-v1",
        60,
        3,
    )
    assert claim is not None
    valid = analysis_for(claim.analysis_id, message)
    mismatch = valid.model_copy(update={"analysis_id": "f" * 64})

    with pytest.raises(ValueError, match="does not match"):
        await store.complete(claim, mismatch)

    assert await store.get_status(claim.analysis_id) == "processing"
    await store.complete(claim, valid)
    assert await store.get_status(claim.analysis_id) == "completed"
    assert await store.get_analysis(claim.analysis_id) == valid
