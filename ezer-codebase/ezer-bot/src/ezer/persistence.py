"""Durable, idempotent persistence for mailbox cursors and analyses.

Only opaque provider identifiers, content hashes, and the final structured analysis are
stored. Raw email bodies, subjects, addresses, credentials, and exception messages are
deliberately excluded from processing rows.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast, runtime_checkable

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr

from ezer.config import EmailAccount, Settings
from ezer.domain import EmailAnalysis, EmailEnvelope

RunStatus = Literal["processing", "retry", "completed", "dead_letter"]
FailureDisposition = Literal["retry", "dead_letter"]

_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_CONTENT_HASH = re.compile(r"^[a-f0-9]{64}$")


class PersistenceError(RuntimeError):
    """Base class for persistence failures without sensitive database context."""


class PersistenceNotOpenError(PersistenceError):
    """Raised when an operation is attempted before ``open``."""


class LeaseLostError(PersistenceError):
    """Raised when a stale worker tries to finish a claim it no longer owns."""


@dataclass(frozen=True, slots=True)
class ProcessingClaim:
    """A fenced lease for one deterministic analysis run."""

    analysis_id: str
    account_id: str
    provider: Literal["gmail", "outlook"]
    provider_message_id: str
    message_ref: str
    content_hash: str
    pipeline_version: str
    attempt: int
    lease_token: str
    lease_until: datetime


@runtime_checkable
class Persistence(Protocol):
    """Structural async persistence contract used by the synchronization service."""

    async def open(self) -> None: ...

    async def close(self) -> None: ...

    async def health(self) -> bool: ...

    async def get_cursor(self, account_id: str) -> str | None: ...

    async def set_cursor(
        self,
        account_id: str,
        expected_cursor: str | None,
        next_cursor: str | None,
    ) -> bool: ...

    async def claim(
        self,
        account: EmailAccount,
        message: EmailEnvelope,
        content_hash: str,
        pipeline_version: str,
        lease_seconds: int,
        max_attempts: int,
    ) -> ProcessingClaim | None: ...

    async def complete(self, claim: ProcessingClaim, result: EmailAnalysis) -> None: ...

    async def fail(
        self,
        claim: ProcessingClaim,
        error: BaseException | str,
        max_attempts: int,
    ) -> FailureDisposition: ...

    async def get_analysis(self, analysis_id: str) -> EmailAnalysis | None: ...

    async def get_status(self, analysis_id: str) -> RunStatus | None: ...


def analysis_id_for(
    account_id: str,
    provider: str,
    provider_message_id: str,
    content_hash: str,
    pipeline_version: str,
) -> str:
    """Build the stable LangGraph thread/analysis ID for a message revision."""

    if not _CONTENT_HASH.fullmatch(content_hash):
        raise ValueError("content_hash must be a lowercase SHA-256 digest")
    # A JSON array gives unambiguous boundaries without retaining email content.
    identity = json.dumps(
        [account_id, provider, provider_message_id, content_hash, pipeline_version],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(identity).hexdigest()


def _safe_error(error: BaseException | str) -> str:
    """Return an operational error label without persisting attacker-controlled text."""

    if isinstance(error, BaseException):
        return f"exception:{type(error).__name__}"[:160]
    if _SAFE_ERROR_CODE.fullmatch(error):
        return error
    digest = hashlib.sha256(error.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"error_sha256:{digest}"


def _validate_claim_input(
    account: EmailAccount,
    message: EmailEnvelope,
    content_hash: str,
    pipeline_version: str,
    lease_seconds: int,
    max_attempts: int,
) -> str:
    if account.id != message.account_id or account.provider != message.provider:
        raise ValueError("message does not belong to the supplied account")
    if not _CONTENT_HASH.fullmatch(content_hash):
        raise ValueError("content_hash must be a lowercase SHA-256 digest")
    if content_hash != message.content_hash():
        raise ValueError("content_hash does not match the supplied message")
    if not pipeline_version or len(pipeline_version) > 64:
        raise ValueError("pipeline_version must contain 1 to 64 characters")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    return analysis_id_for(
        account.id,
        message.provider,
        message.provider_message_id,
        content_hash,
        pipeline_version,
    )


def _claim_from_row(row: Mapping[str, object]) -> ProcessingClaim:
    provider = str(row["provider"])
    if provider not in {"gmail", "outlook"}:
        raise PersistenceError("stored claim has an invalid provider")
    return ProcessingClaim(
        analysis_id=str(row["analysis_id"]),
        account_id=str(row["account_id"]),
        provider=cast(Literal["gmail", "outlook"], provider),
        provider_message_id=str(row["provider_message_id"]),
        message_ref=str(row["message_ref"]),
        content_hash=str(row["content_hash"]),
        pipeline_version=str(row["pipeline_version"]),
        attempt=int(cast(int, row["attempts"])),
        lease_token=str(row["lease_token"]),
        lease_until=datetime.fromtimestamp(float(cast(float, row["lease_until"])), tz=UTC),
    )


def _validate_completion(claim: ProcessingClaim, result: EmailAnalysis) -> str:
    if (
        result.analysis_id != claim.analysis_id
        or result.message_ref != claim.message_ref
        or result.content_hash != claim.content_hash
        or result.pipeline_version != claim.pipeline_version
    ):
        raise ValueError("analysis result does not match its processing claim")
    return result.model_dump_json()


_SQLITE_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS ezer_sync_cursors (
        account_id TEXT PRIMARY KEY,
        cursor TEXT,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ezer_analysis_runs (
        analysis_id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        provider TEXT NOT NULL CHECK (provider IN ('gmail', 'outlook')),
        provider_message_id TEXT NOT NULL,
        message_ref TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        pipeline_version TEXT NOT NULL,
        status TEXT NOT NULL CHECK (
            status IN ('processing', 'retry', 'completed', 'dead_letter')
        ),
        attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        lease_token TEXT,
        lease_until REAL,
        result_json TEXT,
        last_error TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        completed_at REAL,
        UNIQUE (account_id, provider, provider_message_id, content_hash, pipeline_version)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ezer_analysis_claimable_idx
    ON ezer_analysis_runs (status, lease_until, updated_at)
    """,
)

_POSTGRES_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS ezer_sync_cursors (
        account_id VARCHAR(128) PRIMARY KEY,
        cursor TEXT,
        updated_at DOUBLE PRECISION NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ezer_analysis_runs (
        analysis_id CHAR(64) PRIMARY KEY,
        account_id VARCHAR(128) NOT NULL,
        provider VARCHAR(16) NOT NULL CHECK (provider IN ('gmail', 'outlook')),
        provider_message_id TEXT NOT NULL,
        message_ref TEXT NOT NULL,
        content_hash CHAR(64) NOT NULL,
        pipeline_version VARCHAR(64) NOT NULL,
        status VARCHAR(16) NOT NULL CHECK (
            status IN ('processing', 'retry', 'completed', 'dead_letter')
        ),
        attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
        lease_token CHAR(32),
        lease_until DOUBLE PRECISION,
        result_json TEXT,
        last_error VARCHAR(160),
        created_at DOUBLE PRECISION NOT NULL,
        updated_at DOUBLE PRECISION NOT NULL,
        completed_at DOUBLE PRECISION,
        UNIQUE (account_id, provider, provider_message_id, content_hash, pipeline_version)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ezer_analysis_claimable_idx
    ON ezer_analysis_runs (status, lease_until, updated_at)
    """,
)


class SQLitePersistence:
    """SQLite WAL implementation for local development and tests.

    One connection is serialized through an asyncio lock and all blocking sqlite3 work
    runs in a worker thread. ``BEGIN IMMEDIATE`` plus fenced leases also makes separate
    processes safe under SQLite's single-writer model.
    """

    def __init__(self, path: Path | str, *, auto_migrate: bool = True) -> None:
        self._path = str(path)
        self._auto_migrate = auto_migrate
        self._connection: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        async with self._lock:
            if self._connection is not None:
                return
            connection = await asyncio.to_thread(self._connect)
            try:
                if self._auto_migrate:
                    await asyncio.to_thread(self._migrate, connection)
            except BaseException:
                await asyncio.to_thread(connection.close)
                raise
            self._connection = connection

    def _connect(self) -> sqlite3.Connection:
        if self._path != ":memory:":
            Path(self._path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self._path,
            timeout=10.0,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        if self._path != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL").fetchone()
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("PRAGMA wal_autocheckpoint = 1000")
        return connection

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in _SQLITE_SCHEMA:
                connection.execute(statement)
        except BaseException:
            connection.rollback()
            raise
        connection.commit()

    async def close(self) -> None:
        async with self._lock:
            connection = self._connection
            self._connection = None
            if connection is not None:
                await asyncio.to_thread(connection.close)

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise PersistenceNotOpenError("persistence is not open")
        return self._connection

    async def health(self) -> bool:
        async with self._lock:
            if self._connection is None:
                return False
            try:
                row = await asyncio.to_thread(
                    lambda: (
                        self._connection.execute("SELECT 1").fetchone()
                        if self._connection is not None
                        else None
                    )
                )
            except sqlite3.Error:
                return False
            return row is not None and row[0] == 1

    async def get_cursor(self, account_id: str) -> str | None:
        async with self._lock:
            connection = self._require_connection()

            def operation() -> str | None:
                row = connection.execute(
                    "SELECT cursor FROM ezer_sync_cursors WHERE account_id = ?",
                    (account_id,),
                ).fetchone()
                return None if row is None else cast(str | None, row["cursor"])

            return await asyncio.to_thread(operation)

    async def set_cursor(
        self,
        account_id: str,
        expected_cursor: str | None,
        next_cursor: str | None,
    ) -> bool:
        """Advance a cursor with compare-and-set semantics.

        Returning ``False`` means another sync round won the race. This prevents a
        slower worker from moving an opaque provider cursor backwards.
        """

        now = time.time()
        async with self._lock:
            connection = self._require_connection()

            def operation() -> bool:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    if expected_cursor is None:
                        updated = connection.execute(
                            """
                            UPDATE ezer_sync_cursors
                            SET cursor = ?, updated_at = ?
                            WHERE account_id = ? AND cursor IS NULL
                            """,
                            (next_cursor, now, account_id),
                        ).rowcount
                    else:
                        updated = connection.execute(
                            """
                            UPDATE ezer_sync_cursors
                            SET cursor = ?, updated_at = ?
                            WHERE account_id = ? AND cursor = ?
                            """,
                            (next_cursor, now, account_id, expected_cursor),
                        ).rowcount
                    if updated == 1:
                        connection.commit()
                        return True
                    if expected_cursor is not None:
                        connection.commit()
                        return False
                    inserted = connection.execute(
                        """
                        INSERT OR IGNORE INTO ezer_sync_cursors
                            (account_id, cursor, updated_at)
                        VALUES (?, ?, ?)
                        """,
                        (account_id, next_cursor, now),
                    ).rowcount
                    connection.commit()
                    return inserted == 1
                except BaseException:
                    connection.rollback()
                    raise

            return await asyncio.to_thread(operation)

    async def claim(
        self,
        account: EmailAccount,
        message: EmailEnvelope,
        content_hash: str,
        pipeline_version: str,
        lease_seconds: int,
        max_attempts: int,
    ) -> ProcessingClaim | None:
        analysis_id = _validate_claim_input(
            account,
            message,
            content_hash,
            pipeline_version,
            lease_seconds,
            max_attempts,
        )
        now = time.time()
        lease_until = now + lease_seconds
        lease_token = uuid.uuid4().hex

        async with self._lock:
            connection = self._require_connection()

            def operation() -> ProcessingClaim | None:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO ezer_analysis_runs (
                            analysis_id, account_id, provider, provider_message_id,
                            message_ref, content_hash, pipeline_version, status,
                            attempts, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'retry', 0, ?, ?)
                        """,
                        (
                            analysis_id,
                            account.id,
                            message.provider,
                            message.provider_message_id,
                            message.message_ref(),
                            content_hash,
                            pipeline_version,
                            now,
                            now,
                        ),
                    )
                    row = connection.execute(
                        "SELECT * FROM ezer_analysis_runs WHERE analysis_id = ?",
                        (analysis_id,),
                    ).fetchone()
                    if row is None:
                        raise PersistenceError("claim row was not created")

                    status = cast(RunStatus, row["status"])
                    attempts = int(row["attempts"])
                    current_lease = row["lease_until"]
                    actively_leased = (
                        status == "processing"
                        and current_lease is not None
                        and float(current_lease) > now
                    )
                    if status in {"completed", "dead_letter"} or actively_leased:
                        connection.commit()
                        return None
                    if attempts >= max_attempts:
                        connection.execute(
                            """
                            UPDATE ezer_analysis_runs
                            SET status = 'dead_letter', lease_token = NULL,
                                lease_until = NULL, updated_at = ?
                            WHERE analysis_id = ?
                            """,
                            (now, analysis_id),
                        )
                        connection.commit()
                        return None

                    next_attempt = attempts + 1
                    connection.execute(
                        """
                        UPDATE ezer_analysis_runs
                        SET status = 'processing', attempts = ?, lease_token = ?,
                            lease_until = ?, updated_at = ?
                        WHERE analysis_id = ?
                        """,
                        (next_attempt, lease_token, lease_until, now, analysis_id),
                    )
                    claimed = connection.execute(
                        "SELECT * FROM ezer_analysis_runs WHERE analysis_id = ?",
                        (analysis_id,),
                    ).fetchone()
                    connection.commit()
                    if claimed is None:
                        raise PersistenceError("claimed row disappeared")
                    return _claim_from_row(claimed)
                except BaseException:
                    connection.rollback()
                    raise

            return await asyncio.to_thread(operation)

    async def complete(self, claim: ProcessingClaim, result: EmailAnalysis) -> None:
        result_json = _validate_completion(claim, result)
        now = time.time()
        async with self._lock:
            connection = self._require_connection()

            def operation() -> None:
                cursor = connection.execute(
                    """
                    UPDATE ezer_analysis_runs
                    SET status = 'completed', result_json = ?, last_error = NULL,
                        lease_token = NULL, lease_until = NULL,
                        completed_at = ?, updated_at = ?
                    WHERE analysis_id = ? AND status = 'processing'
                      AND lease_token = ? AND attempts = ?
                    """,
                    (
                        result_json,
                        now,
                        now,
                        claim.analysis_id,
                        claim.lease_token,
                        claim.attempt,
                    ),
                )
                if cursor.rowcount != 1:
                    raise LeaseLostError("processing lease is no longer owned")

            await asyncio.to_thread(operation)

    async def fail(
        self,
        claim: ProcessingClaim,
        error: BaseException | str,
        max_attempts: int,
    ) -> FailureDisposition:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        disposition: FailureDisposition = (
            "dead_letter" if claim.attempt >= max_attempts else "retry"
        )
        safe_error = _safe_error(error)
        now = time.time()
        async with self._lock:
            connection = self._require_connection()

            def operation() -> None:
                cursor = connection.execute(
                    """
                    UPDATE ezer_analysis_runs
                    SET status = ?, last_error = ?, lease_token = NULL,
                        lease_until = NULL, updated_at = ?
                    WHERE analysis_id = ? AND status = 'processing'
                      AND lease_token = ? AND attempts = ?
                    """,
                    (
                        disposition,
                        safe_error,
                        now,
                        claim.analysis_id,
                        claim.lease_token,
                        claim.attempt,
                    ),
                )
                if cursor.rowcount != 1:
                    raise LeaseLostError("processing lease is no longer owned")

            await asyncio.to_thread(operation)
        return disposition

    async def get_analysis(self, analysis_id: str) -> EmailAnalysis | None:
        async with self._lock:
            connection = self._require_connection()

            def operation() -> str | None:
                row = connection.execute(
                    """
                    SELECT result_json FROM ezer_analysis_runs
                    WHERE analysis_id = ? AND status = 'completed'
                    """,
                    (analysis_id,),
                ).fetchone()
                return None if row is None else cast(str | None, row["result_json"])

            payload = await asyncio.to_thread(operation)
        if payload is None:
            return None
        try:
            return EmailAnalysis.model_validate_json(payload)
        except ValueError:
            # Pydantic validation details can contain the persisted summary. Suppress the
            # original exception so an operational traceback cannot disclose it.
            raise PersistenceError("stored analysis is invalid") from None

    async def get_status(self, analysis_id: str) -> RunStatus | None:
        """Return lifecycle state only; no result or email-derived text is exposed."""

        async with self._lock:
            connection = self._require_connection()

            def operation() -> str | None:
                row = connection.execute(
                    "SELECT status FROM ezer_analysis_runs WHERE analysis_id = ?",
                    (analysis_id,),
                ).fetchone()
                return None if row is None else str(row["status"])

            status = await asyncio.to_thread(operation)
        if status is None:
            return None
        if status not in {"processing", "retry", "completed", "dead_letter"}:
            raise PersistenceError("stored analysis has an invalid status")
        return cast(RunStatus, status)


type _PostgresConnection = AsyncConnection[dict[str, object]]


class PostgresPersistence:
    """Production PostgreSQL implementation backed by ``psycopg_pool``."""

    def __init__(
        self,
        database_url: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
        auto_migrate: bool = True,
    ) -> None:
        if min_size < 1 or max_size < min_size:
            raise ValueError("invalid PostgreSQL pool size")
        self._database_url = database_url
        self._min_size = min_size
        self._max_size = max_size
        self._auto_migrate = auto_migrate
        self._pool: AsyncConnectionPool[_PostgresConnection] | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        async with self._lock:
            if self._pool is not None:
                return
            pool = cast(
                AsyncConnectionPool[_PostgresConnection],
                AsyncConnectionPool(
                    conninfo=self._database_url,
                    min_size=self._min_size,
                    max_size=self._max_size,
                    open=False,
                    kwargs={"row_factory": dict_row},
                ),
            )
            try:
                await pool.open(wait=True)
                if self._auto_migrate:
                    async with pool.connection() as connection:
                        async with connection.transaction():
                            for statement in _POSTGRES_SCHEMA:
                                await connection.execute(statement)
            except BaseException:
                await pool.close()
                raise
            self._pool = pool

    async def close(self) -> None:
        async with self._lock:
            pool = self._pool
            self._pool = None
            if pool is not None:
                await pool.close()

    def _require_pool(self) -> AsyncConnectionPool[_PostgresConnection]:
        if self._pool is None:
            raise PersistenceNotOpenError("persistence is not open")
        return self._pool

    async def health(self) -> bool:
        if self._pool is None:
            return False
        try:
            async with self._pool.connection() as connection:
                row = await (await connection.execute("SELECT 1 AS ok")).fetchone()
        except Exception:  # health checks intentionally collapse backend failures
            return False
        return row is not None and row["ok"] == 1

    async def get_cursor(self, account_id: str) -> str | None:
        pool = self._require_pool()
        async with pool.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT cursor FROM ezer_sync_cursors WHERE account_id = %s",
                    (account_id,),
                )
            ).fetchone()
        return None if row is None else cast(str | None, row["cursor"])

    async def set_cursor(
        self,
        account_id: str,
        expected_cursor: str | None,
        next_cursor: str | None,
    ) -> bool:
        pool = self._require_pool()
        now = time.time()
        async with pool.connection() as connection, connection.transaction():
            if expected_cursor is None:
                cursor = await connection.execute(
                    """
                    UPDATE ezer_sync_cursors
                    SET cursor = %s, updated_at = %s
                    WHERE account_id = %s AND cursor IS NULL
                    """,
                    (next_cursor, now, account_id),
                )
            else:
                cursor = await connection.execute(
                    """
                    UPDATE ezer_sync_cursors
                    SET cursor = %s, updated_at = %s
                    WHERE account_id = %s AND cursor = %s
                    """,
                    (next_cursor, now, account_id, expected_cursor),
                )
            if cursor.rowcount == 1:
                return True
            if expected_cursor is not None:
                return False
            inserted = await connection.execute(
                """
                INSERT INTO ezer_sync_cursors (account_id, cursor, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT(account_id) DO NOTHING
                """,
                (account_id, next_cursor, now),
            )
            return inserted.rowcount == 1

    async def claim(
        self,
        account: EmailAccount,
        message: EmailEnvelope,
        content_hash: str,
        pipeline_version: str,
        lease_seconds: int,
        max_attempts: int,
    ) -> ProcessingClaim | None:
        analysis_id = _validate_claim_input(
            account,
            message,
            content_hash,
            pipeline_version,
            lease_seconds,
            max_attempts,
        )
        lease_token = uuid.uuid4().hex
        pool = self._require_pool()

        async with pool.connection() as connection, connection.transaction():
            clock_row = await (
                await connection.execute(
                    "SELECT EXTRACT(EPOCH FROM clock_timestamp())::double precision AS now"
                )
            ).fetchone()
            if clock_row is None:
                raise PersistenceError("database clock is unavailable")
            now = float(cast(float, clock_row["now"]))
            lease_until = now + lease_seconds
            await connection.execute(
                """
                INSERT INTO ezer_analysis_runs (
                    analysis_id, account_id, provider, provider_message_id,
                    message_ref, content_hash, pipeline_version, status,
                    attempts, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'retry', 0, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    analysis_id,
                    account.id,
                    message.provider,
                    message.provider_message_id,
                    message.message_ref(),
                    content_hash,
                    pipeline_version,
                    now,
                    now,
                ),
            )
            row = await (
                await connection.execute(
                    "SELECT * FROM ezer_analysis_runs WHERE analysis_id = %s FOR UPDATE",
                    (analysis_id,),
                )
            ).fetchone()
            if row is None:
                raise PersistenceError("claim row was not created")

            status = cast(RunStatus, row["status"])
            attempts = int(cast(int, row["attempts"]))
            current_lease = row["lease_until"]
            actively_leased = (
                status == "processing"
                and current_lease is not None
                and float(cast(float, current_lease)) > now
            )
            if status in {"completed", "dead_letter"} or actively_leased:
                return None
            if attempts >= max_attempts:
                await connection.execute(
                    """
                    UPDATE ezer_analysis_runs
                    SET status = 'dead_letter', lease_token = NULL,
                        lease_until = NULL, updated_at = %s
                    WHERE analysis_id = %s
                    """,
                    (now, analysis_id),
                )
                return None

            next_attempt = attempts + 1
            claimed = await (
                await connection.execute(
                    """
                    UPDATE ezer_analysis_runs
                    SET status = 'processing', attempts = %s, lease_token = %s,
                        lease_until = %s, updated_at = %s
                    WHERE analysis_id = %s
                    RETURNING *
                    """,
                    (next_attempt, lease_token, lease_until, now, analysis_id),
                )
            ).fetchone()
            if claimed is None:
                raise PersistenceError("claim update failed")
            return _claim_from_row(claimed)

    async def complete(self, claim: ProcessingClaim, result: EmailAnalysis) -> None:
        result_json = _validate_completion(claim, result)
        now = time.time()
        pool = self._require_pool()
        async with pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE ezer_analysis_runs
                SET status = 'completed', result_json = %s, last_error = NULL,
                    lease_token = NULL, lease_until = NULL,
                    completed_at = %s, updated_at = %s
                WHERE analysis_id = %s AND status = 'processing'
                  AND lease_token = %s AND attempts = %s
                """,
                (
                    result_json,
                    now,
                    now,
                    claim.analysis_id,
                    claim.lease_token,
                    claim.attempt,
                ),
            )
            if cursor.rowcount != 1:
                await connection.rollback()
                raise LeaseLostError("processing lease is no longer owned")
            await connection.commit()

    async def fail(
        self,
        claim: ProcessingClaim,
        error: BaseException | str,
        max_attempts: int,
    ) -> FailureDisposition:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        disposition: FailureDisposition = (
            "dead_letter" if claim.attempt >= max_attempts else "retry"
        )
        pool = self._require_pool()
        async with pool.connection() as connection:
            cursor = await connection.execute(
                """
                UPDATE ezer_analysis_runs
                SET status = %s, last_error = %s, lease_token = NULL,
                    lease_until = NULL, updated_at = %s
                WHERE analysis_id = %s AND status = 'processing'
                  AND lease_token = %s AND attempts = %s
                """,
                (
                    disposition,
                    _safe_error(error),
                    time.time(),
                    claim.analysis_id,
                    claim.lease_token,
                    claim.attempt,
                ),
            )
            if cursor.rowcount != 1:
                await connection.rollback()
                raise LeaseLostError("processing lease is no longer owned")
            await connection.commit()
        return disposition

    async def get_analysis(self, analysis_id: str) -> EmailAnalysis | None:
        pool = self._require_pool()
        async with pool.connection() as connection:
            row = await (
                await connection.execute(
                    """
                    SELECT result_json FROM ezer_analysis_runs
                    WHERE analysis_id = %s AND status = 'completed'
                    """,
                    (analysis_id,),
                )
            ).fetchone()
        if row is None or row["result_json"] is None:
            return None
        try:
            return EmailAnalysis.model_validate_json(cast(str, row["result_json"]))
        except ValueError:
            raise PersistenceError("stored analysis is invalid") from None

    async def get_status(self, analysis_id: str) -> RunStatus | None:
        pool = self._require_pool()
        async with pool.connection() as connection:
            row = await (
                await connection.execute(
                    "SELECT status FROM ezer_analysis_runs WHERE analysis_id = %s",
                    (analysis_id,),
                )
            ).fetchone()
        if row is None:
            return None
        status = str(row["status"])
        if status not in {"processing", "retry", "completed", "dead_letter"}:
            raise PersistenceError("stored analysis has an invalid status")
        return cast(RunStatus, status)


def _sqlite_path(database_url: str) -> str:
    value = database_url.partition("?")[0]
    prefix = "sqlite:///"
    if not value.startswith(prefix):
        raise ValueError("invalid SQLite database URL")
    path = value[len(prefix) :]
    if not path:
        raise ValueError("SQLite database URL must include a path")
    return path


def create_persistence(
    database_url: str | SecretStr,
    *,
    auto_migrate: bool = True,
    pool_min_size: int = 1,
    pool_max_size: int = 10,
) -> Persistence:
    """Create the appropriate backend without exposing the configured URL."""

    url = database_url.get_secret_value() if isinstance(database_url, SecretStr) else database_url
    if url.startswith("sqlite:///"):
        return SQLitePersistence(_sqlite_path(url), auto_migrate=auto_migrate)
    if url.startswith("postgres://"):
        url = "postgresql://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return PostgresPersistence(
            url,
            min_size=pool_min_size,
            max_size=pool_max_size,
            auto_migrate=auto_migrate,
        )
    raise ValueError("database URL must use sqlite, postgres, or postgresql")


def persistence_from_settings(settings: Settings) -> Persistence:
    return create_persistence(
        settings.database_url,
        auto_migrate=settings.auto_migrate,
        pool_min_size=settings.db_pool_min_size,
        pool_max_size=settings.db_pool_max_size,
    )


__all__ = [
    "FailureDisposition",
    "LeaseLostError",
    "Persistence",
    "PersistenceError",
    "PersistenceNotOpenError",
    "PostgresPersistence",
    "ProcessingClaim",
    "RunStatus",
    "SQLitePersistence",
    "analysis_id_for",
    "create_persistence",
    "persistence_from_settings",
]
