"""W5-A: materials table + scans.extra_metadata + tightened constraints.

Revision ID: 0003_w5a_materials_uploads
Revises: 0002_v7_auth
Create Date: 2026-05-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_w5a_materials_uploads"
down_revision = "0002_v7_auth"
branch_labels = None
depends_on = None


SEED_MATERIALS = ["graphene", "MoS2", "WSe2", "hBN", "WS2"]


def upgrade() -> None:
    # 1) materials table
    op.create_table(
        "materials",
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # 2) seed common materials (idempotent via PK)
    materials_table = sa.table("materials", sa.column("name", sa.Text()))
    op.bulk_insert(
        materials_table,
        [{"name": n} for n in SEED_MATERIALS],
    )

    # 3) scans.extra_metadata JSONB (default empty object)
    op.add_column(
        "scans",
        sa.Column(
            "extra_metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )

    # 4) scans.material — drop partial index, tighten to NOT NULL + FK
    op.drop_index("scans_material_idx", table_name="scans")
    op.alter_column("scans", "material", nullable=False)
    op.create_foreign_key(
        "scans_material_fk",
        "scans",
        "materials",
        ["material"],
        ["name"],
        ondelete="RESTRICT",
    )

    # 5) images.grid_ix / grid_iy — tighten to NOT NULL
    op.alter_column("images", "grid_ix", nullable=False)
    op.alter_column("images", "grid_iy", nullable=False)

    # 6) images — replace partial non-unique index with UNIQUE
    op.drop_index("images_grid_idx", table_name="images")
    op.create_unique_constraint(
        "images_scan_grid_uq",
        "images",
        ["scan_id", "grid_ix", "grid_iy"],
    )


def downgrade() -> None:
    op.drop_constraint("images_scan_grid_uq", "images", type_="unique")
    op.create_index(
        "images_grid_idx",
        "images",
        ["scan_id", "grid_ix", "grid_iy"],
        postgresql_where=sa.text("grid_ix IS NOT NULL AND grid_iy IS NOT NULL"),
    )
    op.alter_column("images", "grid_iy", nullable=True)
    op.alter_column("images", "grid_ix", nullable=True)

    op.drop_constraint("scans_material_fk", "scans", type_="foreignkey")
    op.alter_column("scans", "material", nullable=True)
    op.create_index(
        "scans_material_idx",
        "scans",
        ["material"],
        postgresql_where=sa.text("material IS NOT NULL"),
    )

    op.drop_column("scans", "extra_metadata")
    op.drop_table("materials")
