"""worker_events table for SAM run timing markers

Revision ID: 0007_worker_events
Revises: 0006_procrastinate_init
Create Date: 2026-05-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_worker_events"
down_revision = "0006_procrastinate_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("run_id", sa.Integer, nullable=False),
        sa.Column("event", sa.Text, nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "worker_events_run_id_ts_idx",
        "worker_events",
        ["run_id", sa.text("ts DESC")],
    )
    op.create_index(
        "worker_events_event_ts_idx",
        "worker_events",
        ["event", sa.text("ts DESC")],
    )


def downgrade() -> None:
    op.drop_index("worker_events_event_ts_idx", table_name="worker_events")
    op.drop_index("worker_events_run_id_ts_idx", table_name="worker_events")
    op.drop_table("worker_events")
