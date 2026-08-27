import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = sa.MetaData()

agent_config_table = sa.Table(
    "agent_config",
    metadata,
    sa.Column("config_hash", sa.String(64), primary_key=True),
    sa.Column("label", sa.String(64), nullable=False, unique=True),
    sa.Column("prompt_template", sa.Text, nullable=False),
    sa.Column("model_id", sa.String(255), nullable=False),
    sa.Column("temperature", sa.Float, nullable=False),
    sa.Column("top_p", sa.Float, nullable=False),
    sa.Column("max_tokens", sa.Integer, nullable=False),
    sa.Column("tools_version", sa.String(64), nullable=False),
    sa.Column("context_fields", JSONB, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("config_hash ~ '^[0-9a-f]{64}$'", name="ck_config_hash_format"),
    sa.CheckConstraint("length(trim(label)) > 0", name="ck_config_label_not_blank"),
    sa.CheckConstraint("temperature BETWEEN 0 AND 2", name="ck_temperature_range"),
    sa.CheckConstraint("top_p BETWEEN 0 AND 1", name="ck_top_p_range"),
    sa.CheckConstraint("max_tokens > 0", name="ck_max_tokens_positive"),
)

dossier_table = sa.Table(
    "dossier",
    metadata,
    sa.Column("dossier_id", sa.String(64), primary_key=True),
    sa.Column("revenu_mensuel", sa.Numeric(14, 2), nullable=False),
    sa.Column("charges_mensuelles", sa.Numeric(14, 2), nullable=False),
    sa.Column("montant_demande", sa.Numeric(14, 2), nullable=False),
    sa.Column("duree_mois", sa.Integer, nullable=False),
    sa.Column("anciennete_emploi_mois", sa.Integer, nullable=False),
    sa.Column("type_contrat", sa.String(64), nullable=False),
    sa.Column("age", sa.Integer, nullable=False),
    sa.Column("code_postal", sa.String(5), nullable=False, index=True),
    sa.Column("nb_incidents_passes", sa.Integer, nullable=False),
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
)

internal_list_table = sa.Table(
    "internal_list",
    metadata,
    sa.Column(
        "dossier_id",
        sa.String(64),
        sa.ForeignKey("dossier.dossier_id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("listed_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("length(trim(reason)) > 0", name="ck_internal_reason_not_blank"),
)

run_trace_table = sa.Table(
    "run_trace",
    metadata,
    sa.Column("run_id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "config_hash",
        sa.String(64),
        sa.ForeignKey("agent_config.config_hash", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "dossier_id",
        sa.String(64),
        sa.ForeignKey("dossier.dossier_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("tool_calls", JSONB, nullable=False),
    sa.Column("llm_calls", JSONB, nullable=False),
    sa.Column("decision", sa.String(32), nullable=False),
    sa.Column("justification", sa.Text, nullable=False),
    sa.Column("confidence", sa.Float, nullable=False),
    sa.CheckConstraint("ended_at >= started_at", name="ck_run_trace_time_order"),
    sa.CheckConstraint(
        "decision IN ('ACCORD', 'REFUS', 'INSTRUCTION_MANUELLE')",
        name="ck_run_trace_decision",
    ),
    sa.CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_run_confidence"),
)

trace_event_table = sa.Table(
    "trace_event",
    metadata,
    sa.Column("event_id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "run_id",
        UUID(as_uuid=True),
        sa.ForeignKey("run_trace.run_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    sa.Column("sequence_no", sa.Integer, nullable=False),
    sa.Column("event_type", sa.String(32), nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "event_type IN ('DECISION_EMITTED', 'TOOL_FAILURE', "
        "'GUARDRAIL_OVERRIDE', 'HUMAN_OVERRIDE')",
        name="ck_trace_event_type",
    ),
    sa.CheckConstraint("sequence_no >= 0", name="ck_trace_event_sequence"),
    sa.UniqueConstraint("event_id", "run_id", name="uq_trace_event_id_run"),
    sa.UniqueConstraint("run_id", "sequence_no", name="uq_trace_event_run_sequence"),
)

human_override_table = sa.Table(
    "human_override",
    metadata,
    sa.Column("event_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("run_id", UUID(as_uuid=True), nullable=False, index=True),
    sa.Column("operator_id", sa.String(255), nullable=False),
    sa.Column("original_decision", sa.String(32), nullable=False),
    sa.Column("corrected_decision", sa.String(32), nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(
        ["event_id", "run_id"],
        ["trace_event.event_id", "trace_event.run_id"],
        ondelete="RESTRICT",
    ),
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
)
