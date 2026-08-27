"""Create the initial observable-agent schema.

Revision ID: 20260827_0001
Revises: None
Create Date: 2026-08-27 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260827_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AGENT_CONFIG_ITEMS = (
    sa.Column("config_hash", sa.String(length=64), nullable=False),
    sa.Column("label", sa.String(length=64), nullable=False),
    sa.Column("prompt_template", sa.Text(), nullable=False),
    sa.Column("model_id", sa.String(length=255), nullable=False),
    sa.Column("temperature", sa.Float(), nullable=False),
    sa.Column("top_p", sa.Float(), nullable=False),
    sa.Column("max_tokens", sa.Integer(), nullable=False),
    sa.Column("tools_version", sa.String(length=64), nullable=False),
    sa.Column("context_fields", postgresql.JSONB(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("config_hash ~ '^[0-9a-f]{64}$'", name="ck_config_hash_format"),
    sa.CheckConstraint("length(trim(label)) > 0", name="ck_config_label_not_blank"),
    sa.CheckConstraint("temperature BETWEEN 0 AND 2", name="ck_temperature_range"),
    sa.CheckConstraint("top_p BETWEEN 0 AND 1", name="ck_top_p_range"),
    sa.CheckConstraint("max_tokens > 0", name="ck_max_tokens_positive"),
    sa.PrimaryKeyConstraint("config_hash"),
    sa.UniqueConstraint("label"),
)

_DOSSIER_ITEMS = (
    sa.Column("dossier_id", sa.String(length=64), nullable=False),
    sa.Column("revenu_mensuel", sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column("charges_mensuelles", sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column("montant_demande", sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column("duree_mois", sa.Integer(), nullable=False),
    sa.Column("anciennete_emploi_mois", sa.Integer(), nullable=False),
    sa.Column("type_contrat", sa.String(length=64), nullable=False),
    sa.Column("age", sa.Integer(), nullable=False),
    sa.Column("code_postal", sa.String(length=5), nullable=False),
    sa.Column("nb_incidents_passes", sa.Integer(), nullable=False),
    sa.CheckConstraint("revenu_mensuel > 0", name="ck_dossier_revenu_positive"),
    sa.CheckConstraint(
        "charges_mensuelles >= 0", name="ck_dossier_charges_nonnegative"
    ),
    sa.CheckConstraint("montant_demande > 0", name="ck_dossier_montant_positive"),
    sa.CheckConstraint("duree_mois > 0", name="ck_dossier_duree_positive"),
    sa.CheckConstraint("anciennete_emploi_mois >= 0", name="ck_dossier_anciennete"),
    sa.CheckConstraint("age >= 18", name="ck_dossier_age_adulte"),
    sa.CheckConstraint("length(code_postal) = 5", name="ck_dossier_code_postal"),
    sa.CheckConstraint("nb_incidents_passes >= 0", name="ck_dossier_incidents"),
    sa.PrimaryKeyConstraint("dossier_id"),
)

_INTERNAL_LIST_ITEMS = (
    sa.Column("dossier_id", sa.String(length=64), nullable=False),
    sa.Column("reason", sa.Text(), nullable=False),
    sa.Column("listed_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("length(trim(reason)) > 0", name="ck_internal_reason_not_blank"),
    sa.ForeignKeyConstraint(
        ["dossier_id"], ["dossier.dossier_id"], ondelete="RESTRICT"
    ),
    sa.PrimaryKeyConstraint("dossier_id"),
)

_RUN_TRACE_ITEMS = (
    sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("config_hash", sa.String(length=64), nullable=False),
    sa.Column("dossier_id", sa.String(length=64), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("tool_calls", postgresql.JSONB(), nullable=False),
    sa.Column("llm_calls", postgresql.JSONB(), nullable=False),
    sa.Column("decision", sa.String(length=32), nullable=False),
    sa.Column("justification", sa.Text(), nullable=False),
    sa.Column("confidence", sa.Float(), nullable=False),
    sa.CheckConstraint("ended_at >= started_at", name="ck_run_trace_time_order"),
    sa.CheckConstraint(
        "decision IN ('ACCORD', 'REFUS', 'INSTRUCTION_MANUELLE')",
        name="ck_run_trace_decision",
    ),
    sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_run_confidence"),
    sa.ForeignKeyConstraint(
        ["config_hash"], ["agent_config.config_hash"], ondelete="RESTRICT"
    ),
    sa.ForeignKeyConstraint(
        ["dossier_id"], ["dossier.dossier_id"], ondelete="RESTRICT"
    ),
    sa.PrimaryKeyConstraint("run_id"),
)

_TRACE_EVENT_ITEMS = (
    sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("event_type", sa.String(length=32), nullable=False),
    sa.Column("payload", postgresql.JSONB(), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "event_type IN ('DECISION_EMITTED', 'TOOL_FAILURE', "
        "'GUARDRAIL_OVERRIDE', 'HUMAN_OVERRIDE')",
        name="ck_trace_event_type",
    ),
    sa.ForeignKeyConstraint(["run_id"], ["run_trace.run_id"], ondelete="RESTRICT"),
    sa.PrimaryKeyConstraint("event_id"),
    sa.UniqueConstraint("event_id", "run_id", name="uq_trace_event_id_run"),
)

_HUMAN_OVERRIDE_ITEMS = (
    sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("operator_id", sa.String(length=255), nullable=False),
    sa.Column("original_decision", sa.String(length=32), nullable=False),
    sa.Column("corrected_decision", sa.String(length=32), nullable=False),
    sa.Column("reason", sa.Text(), nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("length(trim(operator_id)) > 0", name="ck_operator_not_blank"),
    sa.CheckConstraint("length(trim(reason)) > 0", name="ck_override_reason_not_blank"),
    sa.CheckConstraint(
        "original_decision IN ('ACCORD', 'REFUS', 'INSTRUCTION_MANUELLE')",
        name="ck_override_original_decision",
    ),
    sa.CheckConstraint(
        "corrected_decision IN ('ACCORD', 'REFUS', 'INSTRUCTION_MANUELLE')",
        name="ck_override_corrected_decision",
    ),
    sa.ForeignKeyConstraint(
        ["event_id", "run_id"],
        ["trace_event.event_id", "trace_event.run_id"],
        ondelete="RESTRICT",
    ),
    sa.PrimaryKeyConstraint("event_id"),
)

_TABLES = (
    ("agent_config", _AGENT_CONFIG_ITEMS),
    ("dossier", _DOSSIER_ITEMS),
    ("internal_list", _INTERNAL_LIST_ITEMS),
    ("run_trace", _RUN_TRACE_ITEMS),
    ("trace_event", _TRACE_EVENT_ITEMS),
    ("human_override", _HUMAN_OVERRIDE_ITEMS),
)

_INDEXES = (
    ("ix_dossier_code_postal", "dossier", ["code_postal"]),
    ("ix_run_trace_config_hash", "run_trace", ["config_hash"]),
    ("ix_run_trace_dossier_id", "run_trace", ["dossier_id"]),
    ("ix_trace_event_run_id", "trace_event", ["run_id"]),
    ("ix_human_override_run_id", "human_override", ["run_id"]),
)


def upgrade() -> None:
    for table_name, items in _TABLES:
        op.create_table(table_name, *items)
    for index_name, table_name, columns in _INDEXES:
        op.create_index(index_name, table_name, columns, unique=False)


def downgrade() -> None:
    for index_name, table_name, _columns in reversed(_INDEXES):
        op.drop_index(index_name, table_name=table_name)
    for table_name, _items in reversed(_TABLES):
        op.drop_table(table_name)
