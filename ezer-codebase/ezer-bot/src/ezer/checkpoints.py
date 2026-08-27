"""Lifecycle management for LangGraph development and production checkpointers."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from ezer.config import Settings
from ezer.domain import (
    ActionItem,
    EmailAnalysis,
    SafetyAssessment,
    SummaryResult,
    TaskExtractionResult,
    TriageResult,
)

_CHECKPOINT_MODEL_ALLOWLIST = (
    ActionItem,
    EmailAnalysis,
    SafetyAssessment,
    SummaryResult,
    TaskExtractionResult,
    TriageResult,
)


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncGenerator[BaseCheckpointSaver[Any]]:
    """Yield an encrypted PostgreSQL saver, or an in-memory saver for local runs.

    Raw email content is passed through LangGraph runtime context rather than state, so it is not
    checkpointed. Encryption still protects the structured classification and summary results.
    """

    if settings.checkpoint_database_url is None:
        yield InMemorySaver()
        return

    serializer: SerializerProtocol = JsonPlusSerializer(
        allowed_msgpack_modules=_CHECKPOINT_MODEL_ALLOWLIST,
    )
    if settings.checkpoint_aes_key is not None:
        key = settings.checkpoint_aes_key.get_secret_value().encode()
        if len(key) not in (16, 24, 32):
            raise ValueError("checkpoint AES key must encode to 16, 24, or 32 bytes")
        serializer = EncryptedSerializer.from_pycryptodome_aes(serde=serializer, key=key)

    url = settings.checkpoint_database_url.get_secret_value()
    if url.startswith("postgres://"):
        url = "postgresql://" + url.removeprefix("postgres://")
    async with AsyncPostgresSaver.from_conn_string(url, serde=serializer) as saver:
        if settings.auto_migrate:
            await saver.setup()
        yield saver


__all__ = ["open_checkpointer"]
