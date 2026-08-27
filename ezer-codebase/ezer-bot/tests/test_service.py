from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest
from pydantic import SecretStr

from ezer.config import GmailAccount, Settings, StaticTokenAuth
from ezer.domain import (
    EmailEnvelope,
    FetchBatch,
    FetchDeadLetter,
    SafetyAssessment,
    SummaryResult,
    TaskExtractionResult,
    TriageResult,
)
from ezer.graph import build_email_analysis_graph
from ezer.persistence import SQLitePersistence, analysis_id_for
from ezer.service import EmailSyncService, pipeline_revision

NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SQLitePersistence]:
    path = tmp_path / "service.db"
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
        body_text="Please review the weekly status.",
    )


def settings(*, max_attempts: int = 3) -> Settings:
    return Settings.model_validate(
        {
            "pipeline_version": "pipeline-v1",
            "processing_max_attempts": max_attempts,
            "processing_lease_seconds": 60,
            "processing_concurrency": 2,
            "provider_concurrency": 2,
        }
    )


class FakeConnector:
    account_id = "primary"
    provider: Literal["gmail"] = "gmail"

    def __init__(self, batches: list[FetchBatch]) -> None:
        self._batches = batches
        self.cursors: list[str | None] = []

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        assert limit == 50
        self.cursors.append(cursor)
        index = min(len(self.cursors) - 1, len(self._batches) - 1)
        return self._batches[index]


class FakeAgentRuntime:
    model_id = "fake-sonnet-5"
    prompt_version = "fake-prompts-v1"

    def __init__(self, *, fail_safety: bool = False) -> None:
        self.fail_safety = fail_safety
        self.safety_calls = 0

    async def assess_safety(self, envelope: EmailEnvelope) -> SafetyAssessment:
        del envelope
        self.safety_calls += 1
        if self.fail_safety:
            raise RuntimeError("simulated model failure")
        return SafetyAssessment(
            risk_level="none",
            phishing_likelihood=0,
            rationale="No security concern.",
            confidence=1,
        )

    async def triage(self, envelope: EmailEnvelope) -> TriageResult:
        del envelope
        return TriageResult(
            category="informational",
            priority="normal",
            needs_human_review=False,
            confidence=1,
            rationale="Informational status.",
        )

    async def summarize(
        self,
        envelope: EmailEnvelope,
        *,
        output_language: str,
        restricted: bool,
    ) -> SummaryResult:
        del envelope
        assert output_language == "fr"
        return SummaryResult(
            summary="Résumé de statut.",
            detected_language="en",
            restricted=restricted,
        )

    async def extract_tasks(self, envelope: EmailEnvelope) -> TaskExtractionResult:
        del envelope
        return TaskExtractionResult()


def service(
    store: SQLitePersistence,
    agents: FakeAgentRuntime,
    *,
    max_attempts: int = 3,
) -> EmailSyncService:
    return EmailSyncService(
        settings=settings(max_attempts=max_attempts),
        persistence=store,
        graph=build_email_analysis_graph(max_attempts=1),
        agents=agents,
    )


async def test_success_then_idempotent_skip_uses_stable_analysis_and_thread_id(
    store: SQLitePersistence,
    account: GmailAccount,
    message: EmailEnvelope,
) -> None:
    agents = FakeAgentRuntime()
    connector = FakeConnector(
        [
            FetchBatch(messages=[message], next_cursor="cursor-1"),
            FetchBatch(messages=[message], next_cursor="cursor-2"),
        ]
    )
    sync = service(store, agents)

    first = await sync.sync_account(account, connector)
    second = await sync.sync_account(account, connector)

    expected_id = analysis_id_for(
        account.id,
        message.provider,
        message.provider_message_id,
        message.content_hash(),
        pipeline_revision(
            "pipeline-v1",
            model_id=agents.model_id,
            prompt_version=agents.prompt_version,
            output_language="fr",
        ),
    )
    assert first.processed == 1
    assert first.skipped == 0
    assert first.cursor_advanced is True
    assert [result.analysis_id for result in first.analyses] == [expected_id]
    assert second.processed == 0
    assert second.skipped == 1
    assert second.failed == 0
    assert second.cursor_advanced is True
    assert agents.safety_calls == 1
    assert connector.cursors == [None, "cursor-1"]
    assert await store.get_cursor(account.id) == "cursor-2"


async def test_nonterminal_failure_does_not_advance_then_dead_letter_allows_progress(
    store: SQLitePersistence,
    account: GmailAccount,
    message: EmailEnvelope,
) -> None:
    agents = FakeAgentRuntime(fail_safety=True)
    connector = FakeConnector([FetchBatch(messages=[message], next_cursor="cursor-1")])
    sync = service(store, agents, max_attempts=2)

    first = await sync.sync_account(account, connector)
    assert first.failed == 1
    assert first.cursor_advanced is False
    assert await store.get_cursor(account.id) is None

    second = await sync.sync_account(account, connector)
    assert second.failed == 1
    assert second.cursor_advanced is False
    assert await store.get_cursor(account.id) is None

    third = await sync.sync_account(account, connector)
    assert third.failed == 0
    assert third.skipped == 1
    assert third.cursor_advanced is True
    assert await store.get_cursor(account.id) == "cursor-1"
    assert agents.safety_calls == 2


async def test_cursor_reset_clears_expired_cursor_with_compare_and_set(
    store: SQLitePersistence,
    account: GmailAccount,
) -> None:
    assert await store.set_cursor(account.id, None, "expired-cursor") is True
    connector = FakeConnector([FetchBatch(messages=[], next_cursor=None, cursor_reset=True)])
    sync = service(store, FakeAgentRuntime())

    report = await sync.sync_account(account, connector)

    assert connector.cursors == ["expired-cursor"]
    assert report.cursor_reset is True
    assert report.cursor_advanced is True
    assert report.fetched == 0
    assert report.failed == 0
    assert await store.get_cursor(account.id) is None


async def test_fetch_dead_letter_is_persisted_terminal_without_calling_llm(
    store: SQLitePersistence,
    account: GmailAccount,
) -> None:
    agents = FakeAgentRuntime()
    dead_letter = FetchDeadLetter(
        account_id=account.id,
        provider=account.provider,
        provider_message_id="oversized-message",
        code="response_too_large",
    )
    connector = FakeConnector(
        [
            FetchBatch(
                messages=[],
                dead_letters=[dead_letter],
                next_cursor="cursor-after-poison",
            )
        ]
    )
    sync = service(store, agents)

    first = await sync.sync_account(account, connector)
    second = await sync.sync_account(account, connector)

    sentinel = EmailEnvelope(
        account_id=account.id,
        provider=account.provider,
        provider_message_id=dead_letter.provider_message_id,
        received_at=datetime.fromtimestamp(0, tz=UTC),
        body_text="ezer-fetch-error:response_too_large",
    )
    revision = pipeline_revision(
        "pipeline-v1",
        model_id=agents.model_id,
        prompt_version=agents.prompt_version,
        output_language="fr",
    )
    analysis_id = analysis_id_for(
        account.id,
        account.provider,
        dead_letter.provider_message_id,
        sentinel.content_hash(),
        revision,
    )

    assert first.fetched == 1
    assert first.failed == 1
    assert first.dead_lettered == 1
    assert first.cursor_advanced is True
    assert first.analyses == []
    assert second.fetched == 1
    assert second.failed == 0
    assert second.skipped == 1
    assert second.dead_lettered == 0
    assert second.cursor_advanced is True
    assert agents.safety_calls == 0
    assert await store.get_status(analysis_id) == "dead_letter"
    assert await store.get_cursor(account.id) == "cursor-after-poison"
