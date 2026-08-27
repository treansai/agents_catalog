"""Application service coordinating provider reads, LangGraph, and persistence."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol, runtime_checkable

from langchain_core.runnables import RunnableConfig

from ezer.agents import AgentRuntime
from ezer.config import EmailAccount, Settings
from ezer.domain import EmailAnalysis, EmailEnvelope, FetchBatch, FetchDeadLetter, SyncReport
from ezer.graph import GRAPH_VERSION, EmailAnalysisGraph, analyze_email
from ezer.observability import Metrics, Timer, log_event
from ezer.persistence import (
    LeaseLostError,
    Persistence,
    ProcessingClaim,
    analysis_id_for,
)

logger = logging.getLogger(__name__)
_FETCH_DEAD_LETTER_RECEIVED_AT = datetime.fromtimestamp(0, tz=UTC)


def pipeline_revision(
    configured_version: str,
    *,
    model_id: str,
    prompt_version: str,
    output_language: str,
) -> str:
    """Fingerprint every behavior input so model/prompt changes invalidate old analyses."""

    fingerprint = hashlib.sha256(
        "\x1f".join(
            (configured_version, GRAPH_VERSION, model_id, prompt_version, output_language)
        ).encode("utf-8")
    ).hexdigest()[:32]
    return f"{configured_version[:31]}-{fingerprint}"


@runtime_checkable
class EmailConnector(Protocol):
    """Structural connector boundary; concrete provider classes need no inheritance."""

    @property
    def account_id(self) -> str: ...

    @property
    def provider(self) -> Literal["gmail", "outlook"]: ...

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch: ...


@dataclass(frozen=True, slots=True)
class _MessageOutcome:
    status: Literal["processed", "skipped", "failed"]
    terminal: bool
    analysis: EmailAnalysis | None = None
    dead_lettered: bool = False


class EmailSyncService:
    """Synchronize one mailbox page with bounded, idempotent processing.

    The provider cursor is compare-and-set only after every message is terminal. A
    completed analysis and an existing dead letter are terminal; another worker's live
    lease is not. This prevents webhook/poller races from skipping a message after a
    worker crash.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        persistence: Persistence,
        graph: EmailAnalysisGraph,
        agents: AgentRuntime,
        metrics: Metrics | None = None,
    ) -> None:
        self._settings = settings
        self._persistence = persistence
        self._graph = graph
        self._agents = agents
        self._metrics = metrics
        self._pipeline_revision = pipeline_revision(
            settings.pipeline_version,
            model_id=agents.model_id,
            prompt_version=agents.prompt_version,
            output_language=settings.output_language,
        )
        self._processing_slots = asyncio.Semaphore(settings.processing_concurrency)
        self._provider_slots = asyncio.Semaphore(settings.provider_concurrency)
        self._account_locks: dict[str, asyncio.Lock] = {}
        self._account_locks_guard = asyncio.Lock()

    async def sync_account(
        self,
        account: EmailAccount,
        connector: EmailConnector,
    ) -> SyncReport:
        """Fetch and process one provider page for ``account``.

        Connector or cursor-store failures are raised to the worker so its outer retry
        policy can back off. Individual analysis failures are fenced, recorded, and
        represented in the returned report without cancelling sibling messages.
        """

        self._validate_connector(account, connector)
        account_lock = await self._account_lock(account.id)
        async with account_lock:
            return await self._sync_locked(account, connector)

    async def _sync_locked(
        self,
        account: EmailAccount,
        connector: EmailConnector,
    ) -> SyncReport:
        timer = Timer()
        account_hash = hashlib.sha256(account.id.encode("utf-8")).hexdigest()[:16]
        current_cursor = await self._persistence.get_cursor(account.id)
        async with self._provider_slots:
            batch = await connector.fetch(current_cursor, self._settings.sync_page_size)

        outcomes = await asyncio.gather(
            *(self._process_message(account, message) for message in batch.messages),
            *(
                self._record_fetch_dead_letter(account, dead_letter)
                for dead_letter in batch.dead_letters
            ),
        )
        processed = sum(outcome.status == "processed" for outcome in outcomes)
        skipped = sum(outcome.status == "skipped" for outcome in outcomes)
        failed = sum(outcome.status == "failed" for outcome in outcomes)
        dead_lettered = sum(outcome.dead_lettered for outcome in outcomes)
        analyses = [outcome.analysis for outcome in outcomes if outcome.analysis is not None]

        all_terminal = all(outcome.terminal for outcome in outcomes)
        cursor_advanced = False
        # An expired provider cursor is explicitly reset to NULL so the next round
        # bootstraps instead of retrying the same invalid checkpoint forever.
        if all_terminal and (batch.next_cursor is not None or batch.cursor_reset):
            cursor_advanced = await self._persistence.set_cursor(
                account.id,
                current_cursor,
                batch.next_cursor,
            )

        report = SyncReport(
            account_id=account.id,
            provider=account.provider,
            fetched=len(batch.messages) + len(batch.dead_letters),
            processed=processed,
            skipped=skipped,
            failed=failed,
            dead_lettered=dead_lettered,
            cursor_advanced=cursor_advanced,
            cursor_reset=batch.cursor_reset,
            analyses=analyses,
        )
        status = "success" if failed == 0 and all_terminal else "partial"
        log_event(
            logger,
            "mailbox_sync_completed",
            account_id_hash=account_hash,
            provider=account.provider,
            status=status,
            duration_ms=timer.milliseconds,
            fetched=report.fetched,
            processed=report.processed,
            skipped=report.skipped,
            failed=report.failed,
            dead_lettered=report.dead_lettered,
            cursor_reset=report.cursor_reset,
        )
        if self._metrics is not None:
            self._metrics.sync_total.labels(
                provider=account.provider,
                status=status,
            ).inc()
            self._metrics.sync_duration.labels(provider=account.provider).observe(timer.seconds)
        return report

    async def _record_fetch_dead_letter(
        self,
        account: EmailAccount,
        dead_letter: FetchDeadLetter,
    ) -> _MessageOutcome:
        """Persist a terminal fetch failure without sending its sentinel to the LLM."""

        if dead_letter.account_id != account.id or dead_letter.provider != account.provider:
            raise ValueError("fetch dead letter does not belong to the supplied account")
        sentinel = EmailEnvelope(
            account_id=dead_letter.account_id,
            provider=dead_letter.provider,
            provider_message_id=dead_letter.provider_message_id,
            received_at=_FETCH_DEAD_LETTER_RECEIVED_AT,
            body_text=f"ezer-fetch-error:{dead_letter.code}",
        )
        async with self._processing_slots:
            content_hash = sentinel.content_hash()
            deterministic_id = analysis_id_for(
                account.id,
                dead_letter.provider,
                dead_letter.provider_message_id,
                content_hash,
                self._pipeline_revision,
            )
            try:
                claim = await self._persistence.claim(
                    account,
                    sentinel,
                    content_hash,
                    self._pipeline_revision,
                    self._settings.processing_lease_seconds,
                    1,
                )
            except Exception as exc:
                self._record_message_metric(account.provider, "claim_failed")
                self._log_message_failure(
                    account,
                    deterministic_id,
                    attempt=None,
                    error=exc,
                )
                return _MessageOutcome(status="failed", terminal=False)

            if claim is None:
                return await self._outcome_for_existing(account, deterministic_id)
            try:
                disposition = await self._persistence.fail(
                    claim,
                    f"fetch:{dead_letter.code}",
                    1,
                )
            except Exception as exc:
                self._record_message_metric(account.provider, "failed")
                self._log_message_failure(
                    account,
                    claim.analysis_id,
                    attempt=claim.attempt,
                    error=exc,
                )
                return _MessageOutcome(status="failed", terminal=False)
            if disposition != "dead_letter":
                raise RuntimeError("permanent fetch failure did not reach dead-letter state")
            self._record_message_metric(account.provider, "dead_letter")
            return _MessageOutcome(
                status="failed",
                terminal=True,
                dead_lettered=True,
            )

    async def _process_message(
        self,
        account: EmailAccount,
        message: EmailEnvelope,
    ) -> _MessageOutcome:
        async with self._processing_slots:
            content_hash = message.content_hash()
            deterministic_id = analysis_id_for(
                account.id,
                message.provider,
                message.provider_message_id,
                content_hash,
                self._pipeline_revision,
            )
            try:
                claim = await self._persistence.claim(
                    account,
                    message,
                    content_hash,
                    self._pipeline_revision,
                    self._settings.processing_lease_seconds,
                    self._settings.processing_max_attempts,
                )
            except Exception as exc:
                self._record_message_metric(account.provider, "claim_failed")
                self._log_message_failure(
                    account,
                    deterministic_id,
                    attempt=None,
                    error=exc,
                )
                return _MessageOutcome(status="failed", terminal=False)

            if claim is None:
                return await self._outcome_for_existing(account, deterministic_id)
            return await self._run_claim(account, message, claim)

    async def _outcome_for_existing(
        self,
        account: EmailAccount,
        analysis_id: str,
    ) -> _MessageOutcome:
        """Conservatively classify an unavailable idempotency claim."""

        try:
            status = await self._persistence.get_status(analysis_id)
            if status == "completed":
                # Validate that the completed payload remains readable, but do not add a
                # cached result to `analyses`: that list represents work done this round.
                analysis = await self._persistence.get_analysis(analysis_id)
                if analysis is None:
                    raise RuntimeError("completed analysis payload is missing")
                self._record_message_metric(account.provider, "skipped")
                return _MessageOutcome(status="skipped", terminal=True)
            if status == "dead_letter":
                # The failure was reported on the attempt that exhausted the budget. It is
                # now durably terminal and must not pin the mailbox cursor forever.
                self._record_message_metric(account.provider, "dead_letter")
                return _MessageOutcome(status="skipped", terminal=True)
            # A live lease (or a conservative race) must prevent cursor advancement.
            self._record_message_metric(account.provider, "leased")
            return _MessageOutcome(status="skipped", terminal=False)
        except Exception as exc:
            self._record_message_metric(account.provider, "lookup_failed")
            self._log_message_failure(
                account,
                analysis_id,
                attempt=None,
                error=exc,
            )
            return _MessageOutcome(status="failed", terminal=False)

    async def _run_claim(
        self,
        account: EmailAccount,
        message: EmailEnvelope,
        claim: ProcessingClaim,
    ) -> _MessageOutcome:
        config: RunnableConfig = {
            "configurable": {"thread_id": claim.analysis_id},
            "metadata": {
                "analysis_id": claim.analysis_id,
                "provider": account.provider,
                "pipeline_version": claim.pipeline_version,
            },
        }
        try:
            analysis = await analyze_email(
                self._graph,
                message,
                self._agents,
                pipeline_version=self._pipeline_revision,
                analysis_id=claim.analysis_id,
                output_language=self._settings.output_language,
                config=config,
            )
            await self._persistence.complete(claim, analysis)
        except asyncio.CancelledError:
            # Cancellation must propagate, but release the claim when the event loop still
            # permits it. Shielding prevents the cleanup await from inheriting cancellation.
            try:
                await asyncio.shield(
                    self._persistence.fail(
                        claim,
                        "cancelled",
                        self._settings.processing_max_attempts,
                    )
                )
            except Exception as cleanup_error:
                self._log_message_failure(
                    account,
                    claim.analysis_id,
                    attempt=claim.attempt,
                    error=cleanup_error,
                )
            raise
        except Exception as exc:
            try:
                await self._persistence.fail(
                    claim,
                    exc,
                    self._settings.processing_max_attempts,
                )
            except LeaseLostError:
                # A newer fenced attempt owns the row. Its outcome is authoritative.
                pass
            self._record_message_metric(account.provider, "failed")
            self._log_message_failure(
                account,
                claim.analysis_id,
                attempt=claim.attempt,
                error=exc,
            )
            return _MessageOutcome(status="failed", terminal=False)

        self._record_message_metric(account.provider, "processed")
        return _MessageOutcome(status="processed", terminal=True, analysis=analysis)

    async def _account_lock(self, account_id: str) -> asyncio.Lock:
        async with self._account_locks_guard:
            lock = self._account_locks.get(account_id)
            if lock is None:
                lock = asyncio.Lock()
                self._account_locks[account_id] = lock
            return lock

    @staticmethod
    def _validate_connector(account: EmailAccount, connector: EmailConnector) -> None:
        if connector.account_id != account.id or connector.provider != account.provider:
            raise ValueError("connector does not match the supplied account")

    def _record_message_metric(self, provider: str, status: str) -> None:
        if self._metrics is not None:
            self._metrics.messages_total.labels(provider=provider, status=status).inc()

    @staticmethod
    def _log_message_failure(
        account: EmailAccount,
        analysis_id: str,
        *,
        attempt: int | None,
        error: BaseException,
    ) -> None:
        account_hash = hashlib.sha256(account.id.encode("utf-8")).hexdigest()[:16]
        log_event(
            logger,
            "email_analysis_failed",
            account_id_hash=account_hash,
            provider=account.provider,
            run_id=analysis_id,
            attempt=attempt,
            error_type=type(error).__name__,
            status="failed",
        )


async def sync_accounts(
    service: EmailSyncService,
    accounts: Sequence[EmailAccount],
    connectors: Sequence[EmailConnector],
) -> list[SyncReport]:
    """Synchronize configured accounts concurrently while preserving input order.

    The caller owns account-level exception policy. ``asyncio.gather`` waits for sibling
    operations, then propagates an infrastructure/provider failure to the worker.
    """

    by_account = {connector.account_id: connector for connector in connectors}
    if len(by_account) != len(connectors):
        raise ValueError("connectors must have unique account IDs")
    missing = [account.id for account in accounts if account.id not in by_account]
    if missing:
        raise ValueError("a configured account has no connector")
    results = await asyncio.gather(
        *(service.sync_account(account, by_account[account.id]) for account in accounts),
        return_exceptions=True,
    )
    reports: list[SyncReport] = []
    errors: list[Exception] = []
    for result in results:
        if isinstance(result, Exception):
            errors.append(result)
        elif isinstance(result, BaseException):
            raise result
        else:
            reports.append(result)
    if errors:
        raise ExceptionGroup("one or more mailbox synchronizations failed", errors)
    return reports


__all__ = ["EmailConnector", "EmailSyncService", "pipeline_revision", "sync_accounts"]
