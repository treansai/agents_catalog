from __future__ import annotations

import os
from logging.config import fileConfig
from typing import Any, cast

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool
from sqlalchemy.engine import Engine

from infrastructure.database.schema import metadata

config = context.config
target_metadata = metadata

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    configured_url = config.get_main_option("sqlalchemy.url")
    if database_url is None and configured_url is None:
        raise RuntimeError("DATABASE_URL or sqlalchemy.url is required")
    return database_url or cast(str, configured_url)


def _offline_configuration() -> dict[str, Any]:
    return {
        "url": _database_url(),
        "target_metadata": target_metadata,
        "literal_binds": True,
        "dialect_opts": {"paramstyle": "named"},
        "compare_type": True,
    }


def run_migrations_offline() -> None:
    context.configure(**_offline_configuration())
    with context.begin_transaction():
        context.run_migrations()


def _engine_settings() -> dict[str, Any]:
    settings = config.get_section(config.config_ini_section) or {}
    settings["sqlalchemy.url"] = _database_url()
    return settings


def _connectable() -> Engine:
    return engine_from_config(
        _engine_settings(), prefix="sqlalchemy.", poolclass=pool.NullPool
    )


def _run_online(connection: Connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with _connectable().connect() as connection:
        _run_online(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
