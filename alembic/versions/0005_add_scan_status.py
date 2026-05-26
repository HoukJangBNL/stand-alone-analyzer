"""D1: add scans.status with backfill of 'ready' for already-complete scans.

Revision ID: add_scan_status
Revises: 0004_w10_projects
Create Date: 2026-05-26

Adds a readiness flag to scans: {'draft', 'ready'}. New rows default to
'draft'; finalize flips to 'ready' when uploaded image count matches
scans.image_count. Existing rows are backfilled to 'ready' if they already
satisfy that invariant at migration time (uploaded_count derived via JOIN
COUNT against images).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "add_scan_status"
down_revision = "0004_w10_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Add the column with a server default so existing rows get 'draft'
    #    automatically, satisfying NOT NULL on legacy data.
    op.add_column(
        "scans",
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
    )

    # 2) Backfill: any scan whose actual image count equals its planned
    #    image_count is already complete and should be marked 'ready'.
    #    JOIN-count via correlated subquery (portable on PG).
    op.execute(
        """
        UPDATE scans
           SET status = 'ready'
         WHERE image_count = (
             SELECT COUNT(*) FROM images WHERE images.scan_id = scans.id
         )
        """
    )


def downgrade() -> None:
    op.drop_column("scans", "status")
