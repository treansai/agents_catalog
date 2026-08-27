from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.config import testcontainers_config

from infrastructure.database.engine import build_engine


def _alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(scope="session")
def migrated_engine() -> Iterator[Engine]:
    testcontainers_config.ryuk_disabled = True
    with PostgresContainer("postgres:18-alpine", driver="psycopg") as postgres:
        database_url = postgres.get_connection_url()
        command.upgrade(_alembic_config(database_url), "head")
        command.check(_alembic_config(database_url))
        engine = build_engine(database_url)
        yield engine
        engine.dispose()


@pytest.fixture
def connection(migrated_engine: Engine) -> Iterator[Connection]:
    with migrated_engine.connect() as database_connection:
        transaction = database_connection.begin()
        yield database_connection
        transaction.rollback()
