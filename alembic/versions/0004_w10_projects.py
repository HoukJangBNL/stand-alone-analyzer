"""W10-A: real projects table + scans/project_users FK rewiring.

Revision ID: 0004_w10_projects
Revises: 0003_w5a_materials_uploads
Create Date: 2026-05-22

Pre-flight (owner manual on saa_test): scripts/db/wipe-saa-test-pre-w10.sql.
Production RDS is empty — pre-flight is a no-op there.

Schema reality at the time this migration was authored (probed against
saa_test post-v7.1):
    - scans.project_id: ABSENT  → simply add as FK NOT NULL
    - images.project_id: ABSENT → conditional drop is a no-op
    - project_users.project_id: PRESENT (TEXT NOT NULL, PK
      `project_users_pkey` over (project_id, user_id)) → drop PK, drop col,
      re-add as FK, re-create PK.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_w10_projects"
down_revision = "0003_w5a_materials_uploads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Create the new projects table
    op.create_table(
        "projects",
        sa.Column(
            "id",
            sa.Text(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()::text"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("owner_id", "name", name="projects_owner_name_uq"),
    )
    op.create_foreign_key(
        "projects_owner_fk",
        "projects",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # 2) scans.project_id — column does not exist in v7.1 schema; add as FK
    #    with RESTRICT (D2 retention policy). Pre-flight wipe ensures scans
    #    is empty so NOT NULL add is safe.
    op.add_column(
        "scans",
        sa.Column(
            "project_id",
            sa.Text(),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "scans_project_fk",
        "scans",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("scans_project_idx", "scans", ["project_id"])

    # 3) project_users.project_id — drop loose TEXT, re-add as FK with CASCADE
    op.drop_constraint("project_users_pkey", "project_users", type_="primary")
    op.drop_column("project_users", "project_id")
    op.add_column(
        "project_users",
        sa.Column(
            "project_id",
            sa.Text(),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "project_users_project_fk",
        "project_users",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_primary_key(
        "project_users_pkey", "project_users", ["project_id", "user_id"]
    )

    # 4) images.project_id — conditional drop (column absent in v7.1; this
    #    block exists for envs where an earlier denorm column may linger).
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'images'
                  AND column_name = 'project_id'
                  AND table_schema = 'public'
            ) THEN
                ALTER TABLE images DROP COLUMN project_id;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # Re-add images.project_id as loose TEXT (no FK — matches pre-W10 state
    # for envs that had the denorm column; harmless for v7.1 which never had it).
    op.add_column("images", sa.Column("project_id", sa.Text(), nullable=True))

    # Revert project_users.project_id FK → loose TEXT
    op.drop_constraint("project_users_pkey", "project_users", type_="primary")
    op.drop_constraint(
        "project_users_project_fk", "project_users", type_="foreignkey"
    )
    op.drop_column("project_users", "project_id")
    op.add_column(
        "project_users", sa.Column("project_id", sa.Text(), nullable=False)
    )
    op.create_primary_key(
        "project_users_pkey", "project_users", ["project_id", "user_id"]
    )

    # Revert scans.project_id FK — drop entirely (v7.1 had no project_id on
    # scans, so we drop rather than re-add as loose TEXT to round-trip cleanly).
    op.drop_index("scans_project_idx", table_name="scans")
    op.drop_constraint("scans_project_fk", "scans", type_="foreignkey")
    op.drop_column("scans", "project_id")

    # projects_owner_fk is auto-dropped with the table.
    op.drop_table("projects")
