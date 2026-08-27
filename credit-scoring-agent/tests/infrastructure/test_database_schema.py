from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect

from infrastructure.database.schema import metadata

EXPECTED_TABLES = {
    "agent_config",
    "dossier",
    "human_override",
    "internal_list",
    "run_trace",
    "trace_event",
}


def test_metadata_declares_all_required_tables() -> None:
    assert set(metadata.tables) == EXPECTED_TABLES


def test_initial_migration_creates_all_tables(migrated_engine: Engine) -> None:
    table_names = set(inspect(migrated_engine).get_table_names())
    assert table_names == EXPECTED_TABLES | {"alembic_version"}


def test_human_override_is_a_trace_event_child(migrated_engine: Engine) -> None:
    foreign_keys = inspect(migrated_engine).get_foreign_keys("human_override")
    trace_key = next(
        key for key in foreign_keys if key["referred_table"] == "trace_event"
    )
    assert trace_key["constrained_columns"] == ["event_id", "run_id"]
    assert trace_key["options"]["ondelete"] == "RESTRICT"


def test_audit_events_cannot_be_cascade_deleted(migrated_engine: Engine) -> None:
    foreign_keys = inspect(migrated_engine).get_foreign_keys("trace_event")
    run_key = next(key for key in foreign_keys if key["referred_table"] == "run_trace")
    assert run_key["options"]["ondelete"] == "RESTRICT"


def test_migration_can_downgrade_and_reupgrade(migrated_engine: Engine) -> None:
    url = migrated_engine.url.render_as_string(hide_password=False)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config, "base")
    assert inspect(migrated_engine).get_table_names() == ["alembic_version"]
    command.upgrade(config, "head")
    assert set(inspect(migrated_engine).get_table_names()) == EXPECTED_TABLES | {
        "alembic_version"
    }
