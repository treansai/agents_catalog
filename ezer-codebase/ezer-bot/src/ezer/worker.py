"""Polling worker with graceful signal handling and a one-shot mode."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Protocol, cast

from ezer.bootstrap import Runtime, RuntimeRole, bootstrap
from ezer.config import Settings
from ezer.domain import SyncReport
from ezer.observability import Timer, exception_leaves, log_event, safe_exception_fields

logger = logging.getLogger(__name__)


def _log_poll_failure(error: BaseException, *, duration_ms: int) -> None:
    leaves = exception_leaves(error)
    for leaf in leaves:
        log_event(
            logger,
            "worker_poll_error",
            status="failed",
            **safe_exception_fields(leaf),
        )
    log_event(
        logger,
        "worker_poll_failed",
        status="failed",
        duration_ms=duration_ms,
        error_type=type(error).__name__,
        failure_count=len(leaves),
    )


class RuntimeContextFactory(Protocol):
    def __call__(
        self,
        role: RuntimeRole,
        *,
        settings: Settings | None = None,
    ) -> AbstractAsyncContextManager[Runtime]: ...


def _install_signal_handlers(stop_event: asyncio.Event) -> Callable[[], None]:
    """Install best-effort SIGINT/SIGTERM handlers and return their cleanup."""

    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError, RuntimeError, ValueError:
            continue
        installed.append(signum)

    def cleanup() -> None:
        for signum in installed:
            loop.remove_signal_handler(signum)

    return cleanup


async def sync_once(
    settings: Settings | None = None,
    *,
    account_ids: Sequence[str] | None = None,
    limit: int | None = None,
    runtime_factory: RuntimeContextFactory | None = None,
) -> list[SyncReport]:
    """Open the sync runtime, process one provider page, and close everything."""

    factory = runtime_factory or cast(RuntimeContextFactory, bootstrap)
    async with factory("sync", settings=settings) as runtime:
        return await runtime.sync(account_ids=account_ids, limit=limit)


async def run_worker(
    settings: Settings | None = None,
    *,
    once: bool = False,
    stop_event: asyncio.Event | None = None,
    runtime_factory: RuntimeContextFactory | None = None,
) -> list[SyncReport]:
    """Poll all configured accounts until stopped.

    A signal requests shutdown but does not cancel an in-flight page, allowing cursor and
    persistence updates to finish. In ``once`` mode, infrastructure errors propagate so callers
    and schedulers receive a non-zero result.
    """

    factory = runtime_factory or cast(RuntimeContextFactory, bootstrap)
    shutdown = stop_event or asyncio.Event()

    def no_signal_cleanup() -> None:
        return None

    cleanup_signals: Callable[[], None] = no_signal_cleanup
    if stop_event is None and not once:
        cleanup_signals = _install_signal_handlers(shutdown)

    last_reports: list[SyncReport] = []
    try:
        async with factory("worker", settings=settings) as runtime:
            while once or not shutdown.is_set():
                timer = Timer()
                try:
                    last_reports = await runtime.sync()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    _log_poll_failure(exc, duration_ms=timer.milliseconds)
                    if once:
                        raise
                else:
                    log_event(
                        logger,
                        "worker_poll_completed",
                        status="success",
                        duration_ms=timer.milliseconds,
                        fetched=sum(report.fetched for report in last_reports),
                        processed=sum(report.processed for report in last_reports),
                        skipped=sum(report.skipped for report in last_reports),
                        failed=sum(report.failed for report in last_reports),
                        dead_lettered=sum(report.dead_lettered for report in last_reports),
                    )

                if once or shutdown.is_set():
                    break
                try:
                    await asyncio.wait_for(
                        shutdown.wait(),
                        timeout=runtime.settings.poll_interval_seconds,
                    )
                except TimeoutError:
                    pass
    finally:
        cleanup_signals()

    return last_reports


__all__ = ["run_worker", "sync_once"]
