"""Short-lived database read ports used by the run orchestrator."""

from sqlalchemy import Engine

from domain.configuration import AgentConfig
from domain.dossier import Dossier
from infrastructure.database.repositories import (
    get_agent_config,
    get_dossier,
    get_internal_list_entry,
)


def lookup_agent_config(engine: Engine, config_hash: str) -> AgentConfig | None:
    with engine.connect() as connection:
        record = get_agent_config(connection, config_hash)
    return None if record is None else record.config


def lookup_dossier(engine: Engine, dossier_id: str) -> Dossier | None:
    with engine.connect() as connection:
        return get_dossier(connection, dossier_id)


def lookup_internal_reason(engine: Engine, dossier_id: str) -> str | None:
    with engine.connect() as connection:
        entry = get_internal_list_entry(connection, dossier_id)
    return None if entry is None else entry.reason
