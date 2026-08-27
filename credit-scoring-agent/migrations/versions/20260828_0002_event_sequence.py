"""Preserve the exact event order for audit reconstruction.

Revision ID: 20260828_0002
Revises: 20260827_0001
Create Date: 2026-08-28 00:00:00
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_0002"
down_revision: str | None = "20260827_0001"
branch_labels: str | None = None
depends_on: str | None = None

_BACKFILL = """
WITH ranked AS (
  SELECT event_id, ROW_NUMBER() OVER (
    PARTITION BY run_id ORDER BY occurred_at, event_id
  ) - 1 AS sequence_no
  FROM trace_event
)
UPDATE trace_event
SET sequence_no = ranked.sequence_no
FROM ranked
WHERE trace_event.event_id = ranked.event_id
"""


def upgrade() -> None:
    op.add_column("trace_event", sa.Column("sequence_no", sa.Integer(), nullable=True))
    op.execute(_BACKFILL)
    op.alter_column("trace_event", "sequence_no", nullable=False)
    op.create_check_constraint(
        "ck_trace_event_sequence", "trace_event", "sequence_no >= 0"
    )
    op.create_unique_constraint(
        "uq_trace_event_run_sequence", "trace_event", ["run_id", "sequence_no"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_trace_event_run_sequence", "trace_event", type_="unique")
    op.drop_constraint("ck_trace_event_sequence", "trace_event", type_="check")
    op.drop_column("trace_event", "sequence_no")
