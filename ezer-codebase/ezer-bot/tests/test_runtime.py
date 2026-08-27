from __future__ import annotations

import argparse
import asyncio
import json
import runpy
import signal
from collections.abc import AsyncGenerator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Literal, cast

import httpx
import pytest
import uvicorn

import ezer.cli as cli
import ezer.worker as worker
from ezer.agents import AgentRuntime
from ezer.bootstrap import (
    Runtime,
    RuntimeRole,
    SyncLimitError,
    UnknownAccountError,
    _close_connector,
    bootstrap,
)
from ezer.config import EmailAccount, Settings
from ezer.connectors.base import AuthenticationError
from ezer.domain import (
    EmailAnalysis,
    EmailEnvelope,
    FetchBatch,
    SafetyAssessment,
    SummaryResult,
    SyncReport,
    TaskExtractionResult,
    TriageResult,
)
from ezer.persistence import (
    FailureDisposition,
    Persistence,
    ProcessingClaim,
    RunStatus,
)
from ezer.service import EmailConnector


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "anthropic_api_key": "test-anthropic-key",
        "accounts_json": json.dumps(
            [
                {
                    "provider": "gmail",
                    "id": "primary",
                    "auth": {"type": "access_token", "access_token": "test-token"},
                }
            ]
        ),
        "sync_page_size": 10,
    }
    values.update(updates)
    return Settings(**values)  # type: ignore[arg-type]


class _Persistence:
    def __init__(self, events: list[str] | None = None, *, healthy: bool = True) -> None:
        self.events = events if events is not None else []
        self.healthy = healthy

    async def open(self) -> None:
        self.events.append("persistence_open")

    async def close(self) -> None:
        self.events.append("persistence_close")

    async def health(self) -> bool:
        return self.healthy

    async def get_cursor(self, account_id: str) -> str | None:
        assert account_id == "primary"
        return None

    async def set_cursor(
        self,
        account_id: str,
        expected_cursor: str | None,
        next_cursor: str | None,
    ) -> bool:
        del account_id, expected_cursor, next_cursor
        return True

    async def claim(
        self,
        account: EmailAccount,
        message: EmailEnvelope,
        content_hash: str,
        pipeline_version: str,
        lease_seconds: int,
        max_attempts: int,
    ) -> ProcessingClaim | None:
        del account, message, content_hash, pipeline_version, lease_seconds, max_attempts
        return None

    async def complete(self, claim: ProcessingClaim, result: EmailAnalysis) -> None:
        del claim, result

    async def fail(
        self,
        claim: ProcessingClaim,
        error: BaseException | str,
        max_attempts: int,
    ) -> FailureDisposition:
        del claim, error, max_attempts
        return "retry"

    async def get_analysis(self, analysis_id: str) -> EmailAnalysis | None:
        del analysis_id
        return None

    async def get_status(self, analysis_id: str) -> RunStatus | None:
        del analysis_id
        return None


class _Agents:
    model_id = "fake-sonnet"
    prompt_version = "fake-prompts"

    async def assess_safety(self, envelope: EmailEnvelope) -> SafetyAssessment:
        del envelope
        raise AssertionError("empty connector batches must not invoke agents")

    async def triage(self, envelope: EmailEnvelope) -> TriageResult:
        del envelope
        raise AssertionError("empty connector batches must not invoke agents")

    async def summarize(
        self,
        envelope: EmailEnvelope,
        *,
        output_language: str,
        restricted: bool,
    ) -> SummaryResult:
        del envelope, output_language, restricted
        raise AssertionError("empty connector batches must not invoke agents")

    async def extract_tasks(self, envelope: EmailEnvelope) -> TaskExtractionResult:
        del envelope
        raise AssertionError("empty connector batches must not invoke agents")


class _Connector:
    provider: Literal["gmail"] = "gmail"

    def __init__(self, account_id: str, events: list[str]) -> None:
        self.account_id = account_id
        self.events = events
        self.limits: list[int] = []

    async def fetch(self, cursor: str | None, limit: int) -> FetchBatch:
        assert cursor is None
        self.limits.append(limit)
        return FetchBatch(messages=[], next_cursor=None)

    async def aclose(self) -> None:
        self.events.append("connector_close")


async def test_bootstrap_wires_limits_selection_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    persistence = _Persistence(events)
    connector = _Connector("primary", events)
    clients: list[httpx.AsyncClient] = []

    def make_persistence(settings: Settings) -> Persistence:
        del settings
        return persistence

    def make_connector(
        account: EmailAccount,
        client: httpx.AsyncClient,
        *,
        max_body_chars: int,
        max_concurrency: int,
        msal_cache_dir: Path,
        msal_cache_encryption_key: bytes | None,
    ) -> EmailConnector:
        assert account.id == "primary"
        assert max_body_chars == 40_000
        assert max_concurrency == 4
        assert msal_cache_dir == Path("var/msal-cache")
        assert msal_cache_encryption_key is None
        clients.append(client)
        return connector

    monkeypatch.setattr("ezer.bootstrap.configure_logging", lambda level: None)
    async with bootstrap(
        "sync",
        settings=_settings(),
        persistence_factory=make_persistence,
        connector_factory=make_connector,
        agent_factory=lambda settings: cast(AgentRuntime, _Agents()),
    ) as runtime:
        assert runtime.select_accounts() == runtime.accounts
        assert [account.id for account in runtime.select_accounts(["primary", "primary"])] == [
            "primary"
        ]
        with pytest.raises(UnknownAccountError):
            runtime.select_accounts([])
        with pytest.raises(UnknownAccountError):
            runtime.select_accounts(["missing"])
        with pytest.raises(SyncLimitError):
            await runtime.sync(limit=11)

        limited = await runtime.sync(account_ids=["primary"], limit=3)
        default = await runtime.sync()

        assert limited[0].account_id == "primary"
        assert default[0].provider == "gmail"
        assert connector.limits == [3, 10]
        assert clients[0].is_closed is False

    assert events == ["persistence_open", "connector_close", "persistence_close"]
    assert clients[0].is_closed is True


async def test_bootstrap_cleans_up_after_partial_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    persistence = _Persistence(events)
    clients: list[httpx.AsyncClient] = []

    def fail_connector(
        account: EmailAccount,
        client: httpx.AsyncClient,
        *,
        max_body_chars: int,
        max_concurrency: int,
        msal_cache_dir: Path,
        msal_cache_encryption_key: bytes | None,
    ) -> EmailConnector:
        del account, max_body_chars, max_concurrency, msal_cache_dir, msal_cache_encryption_key
        clients.append(client)
        raise RuntimeError("connector startup failed")

    monkeypatch.setattr("ezer.bootstrap.configure_logging", lambda level: None)
    with pytest.raises(RuntimeError, match="startup"):
        async with bootstrap(
            "sync",
            settings=_settings(),
            persistence_factory=lambda settings: persistence,
            connector_factory=fail_connector,
            agent_factory=lambda settings: cast(AgentRuntime, _Agents()),
        ):
            pytest.fail("startup failure must prevent entering the runtime")

    assert events == ["persistence_open", "persistence_close"]
    assert clients[0].is_closed is True


async def test_connector_cleanup_accepts_missing_and_synchronous_close() -> None:
    class NoClose:
        pass

    closed: list[bool] = []

    class SyncClose:
        def aclose(self) -> None:
            closed.append(True)

    await _close_connector(cast(EmailConnector, NoClose()))
    await _close_connector(cast(EmailConnector, SyncClose()))
    assert closed == [True]


class _WorkerRuntime:
    def __init__(
        self,
        outcomes: list[object],
        *,
        stop_event: asyncio.Event | None = None,
        stop_after: int | None = None,
        poll_interval: float = 0.001,
    ) -> None:
        self.settings = Settings.model_construct(poll_interval_seconds=poll_interval)
        self.outcomes = outcomes
        self.stop_event = stop_event
        self.stop_after = stop_after
        self.calls: list[tuple[Sequence[str] | None, int | None]] = []

    async def sync(
        self,
        *,
        account_ids: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[SyncReport]:
        self.calls.append((account_ids, limit))
        if self.stop_event is not None and self.stop_after == len(self.calls):
            self.stop_event.set()
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return cast(list[SyncReport], outcome)


class _RuntimeFactory:
    def __init__(self, runtime: _WorkerRuntime) -> None:
        self.runtime = runtime
        self.roles: list[str] = []
        self.exited = False

    def __call__(
        self,
        role: RuntimeRole,
        *,
        settings: Settings | None = None,
    ) -> AbstractAsyncContextManager[Runtime]:
        del settings
        self.roles.append(role)

        @asynccontextmanager
        async def manager() -> AsyncGenerator[Runtime]:
            try:
                yield cast(Runtime, self.runtime)
            finally:
                self.exited = True

        return manager()


def _report(*, failed: int = 0) -> SyncReport:
    return SyncReport(account_id="primary", provider="gmail", fetched=1, failed=failed)


async def test_sync_once_forwards_account_selection_and_limit() -> None:
    runtime = _WorkerRuntime([[_report()]])
    factory = _RuntimeFactory(runtime)

    reports = await worker.sync_once(
        Settings.model_construct(),
        account_ids=["primary"],
        limit=2,
        runtime_factory=factory,
    )

    assert reports[0].fetched == 1
    assert runtime.calls == [(["primary"], 2)]
    assert factory.roles == ["sync"]
    assert factory.exited is True


async def test_worker_retries_after_poll_failure_then_stops() -> None:
    stop_event = asyncio.Event()
    runtime = _WorkerRuntime(
        [RuntimeError("temporary failure"), [_report()]],
        stop_event=stop_event,
        stop_after=2,
    )
    factory = _RuntimeFactory(runtime)

    reports = await worker.run_worker(stop_event=stop_event, runtime_factory=factory)

    assert reports == [_report()]
    assert len(runtime.calls) == 2
    assert factory.roles == ["worker"]
    assert factory.exited is True


def test_worker_logs_safe_exception_group_leaves(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        worker,
        "log_event",
        lambda logger, event, **fields: events.append((event, fields)),
    )
    error = ExceptionGroup(
        "mailbox failed",
        [
            AuthenticationError(
                provider="outlook",
                account_id="outlook-work",
                operation="oauth_token",
                code="invalid_request_aadsts9002346",
            )
        ],
    )

    worker._log_poll_failure(error, duration_ms=12)

    assert events[0][0] == "worker_poll_error"
    assert events[0][1]["provider"] == "outlook"
    assert events[0][1]["operation"] == "oauth_token"
    assert events[0][1]["error_code"] == "invalid_request_aadsts9002346"
    assert events[0][1]["account_id_hash"] != "outlook-work"
    assert events[1] == (
        "worker_poll_failed",
        {
            "duration_ms": 12,
            "error_type": "ExceptionGroup",
            "failure_count": 1,
            "status": "failed",
        },
    )


async def test_worker_once_propagates_failure_and_cancellation() -> None:
    failure_factory = _RuntimeFactory(_WorkerRuntime([RuntimeError("failure")]))
    with pytest.raises(RuntimeError, match="failure"):
        await worker.run_worker(once=True, runtime_factory=failure_factory)
    assert failure_factory.exited is True

    cancelled_factory = _RuntimeFactory(_WorkerRuntime([asyncio.CancelledError()]))
    with pytest.raises(asyncio.CancelledError):
        await worker.run_worker(once=True, runtime_factory=cancelled_factory)
    assert cancelled_factory.exited is True


async def test_worker_signal_handler_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    added: list[signal.Signals] = []
    removed: list[signal.Signals] = []

    class Loop:
        def add_signal_handler(
            self,
            signum: signal.Signals,
            callback: object,
        ) -> None:
            del callback
            if signum == signal.SIGTERM:
                raise NotImplementedError
            added.append(signum)

        def remove_signal_handler(self, signum: signal.Signals) -> bool:
            removed.append(signum)
            return True

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: Loop())
    cleanup = worker._install_signal_handlers(asyncio.Event())
    cleanup()

    assert added == [signal.SIGINT]
    assert removed == [signal.SIGINT]


class _DoctorSettings:
    def __init__(self) -> None:
        self.roles: list[str] = []

    def validate_for_role(self, role: object) -> None:
        self.roles.append(str(role))

    def load_accounts(self) -> list[EmailAccount]:
        return []


async def test_doctor_checks_health_checkpointer_and_always_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _DoctorSettings()
    persistence = _Persistence()
    checkpoint_entries: list[bool] = []

    @asynccontextmanager
    async def checkpointer_context(settings_value: Settings) -> AsyncGenerator[object]:
        del settings_value
        checkpoint_entries.append(True)
        yield object()

    monkeypatch.setattr(cli, "persistence_from_settings", lambda value: persistence)
    monkeypatch.setattr(cli, "open_checkpointer", checkpointer_context)

    await cli.doctor(cast(Settings, settings))

    assert settings.roles == ["sync"]
    assert checkpoint_entries == [True]
    assert persistence.events == ["persistence_open", "persistence_close"]

    unhealthy = _Persistence(healthy=False)
    monkeypatch.setattr(cli, "persistence_from_settings", lambda value: unhealthy)
    with pytest.raises(RuntimeError, match="health"):
        await cli.doctor(cast(Settings, settings))
    assert unhealthy.events == ["persistence_open", "persistence_close"]


def test_run_api_validates_role_and_disables_uvicorn_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _DoctorSettings()
    application = object()
    call: dict[str, object] = {}

    monkeypatch.setattr(cli, "create_app", lambda value: application)

    def fake_run(app: object, **kwargs: object) -> None:
        call["app"] = app
        call.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    cli._run_api(
        cast(Settings, settings),
        argparse.Namespace(host="127.0.0.2", port=9000),
    )

    assert settings.roles == ["api"]
    assert call["app"] is application
    assert call["host"] == "127.0.0.2"
    assert call["port"] == 9000
    assert call["log_config"] is None
    assert call["access_log"] is False


def test_cli_dispatches_worker_sync_and_doctor(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings.model_construct(log_level="INFO")
    calls: list[tuple[str, object]] = []

    async def fake_worker(
        value: Settings,
        *,
        once: bool,
    ) -> list[SyncReport]:
        assert value is settings
        calls.append(("worker", once))
        return [_report()]

    async def fake_sync(
        value: Settings,
        *,
        account_ids: Sequence[str] | None,
        limit: int | None,
    ) -> list[SyncReport]:
        assert value is settings
        calls.append(("sync", (account_ids, limit)))
        return [_report()]

    async def fake_doctor(value: Settings) -> None:
        assert value is settings
        calls.append(("doctor", True))

    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "configure_logging", lambda level: None)
    monkeypatch.setattr(cli, "run_worker", fake_worker)
    monkeypatch.setattr(cli, "sync_once", fake_sync)
    monkeypatch.setattr(cli, "doctor", fake_doctor)

    assert cli.main(["worker", "--once"]) == 0
    assert '"account_id":"primary"' in capsys.readouterr().out
    assert cli.main(["sync", "--account", "primary", "--limit", "2"]) == 0
    assert '"analyses"' not in capsys.readouterr().out
    assert cli.main(["doctor"]) == 0
    assert capsys.readouterr().out == '{"status":"ok"}\n'
    assert calls == [
        ("worker", True),
        ("sync", (["primary"], 2)),
        ("doctor", True),
    ]


def test_cli_returns_safe_statuses_for_failure_and_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "configure_logging", lambda level: None)
    monkeypatch.setattr(cli, "log_event", lambda logger, event, **fields: None)

    def fail_settings() -> Settings:
        raise RuntimeError("private configuration detail")

    monkeypatch.setattr(cli, "Settings", fail_settings)
    assert cli.main(["doctor"]) == 1

    monkeypatch.setattr(cli, "Settings", lambda: Settings.model_construct(log_level="INFO"))

    def interrupt(settings: Settings, arguments: argparse.Namespace) -> None:
        del settings, arguments
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_run_api", interrupt)
    assert cli.main(["api"]) == 130


def test_cli_rejects_non_positive_sync_limit() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["sync", "--limit", "0"])


def test_python_module_entrypoint_propagates_exit_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "main", lambda: 7)
    with pytest.raises(SystemExit) as caught:
        runpy.run_module("ezer.__main__", run_name="__main__")
    assert caught.value.code == 7
