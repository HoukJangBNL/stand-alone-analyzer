"""v7 auth + ACL + usage_events

Revision ID: 0002_v7_auth
Revises: 0001_initial_v6
Create Date: 2026-05-21
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002_v7_auth"
down_revision: Union[str, None] = "0001_initial_v6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Tables whose ``created_by_id`` FK targets ``users(id)`` and therefore
# must be widened from BIGINT to UUID alongside the users.id widening.
_USER_FK_TABLES: tuple[str, ...] = (
    "scans",
    "upload_sessions",
    "analyses",
    "domain_analyses",
    "flake_analyses",
    "flake_curations",
)


def upgrade() -> None:
    # pgcrypto provides gen_random_uuid(); idempotent across re-runs.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    op.execute("CREATE TYPE user_role AS ENUM ('member', 'reader', 'operator', 'admin');")
    op.execute("CREATE TYPE project_role AS ENUM ('viewer', 'editor');")

    # Shadow table — built alongside v6 ``users`` so we can copy rows and
    # remember the legacy BIGSERIAL id via ``legacy_id`` for FK rewiring.
    op.execute("""
        CREATE TABLE users_v7 (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            username           TEXT,
            cognito_sub        TEXT UNIQUE,
            email              TEXT,
            email_verified_at  TIMESTAMPTZ,
            organization       TEXT,
            role               user_role NOT NULL DEFAULT 'member',
            deactivated_at     TIMESTAMPTZ,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            legacy_id          BIGINT UNIQUE
        );
    """)
    # Promote every legacy user (currently just 'system') to admin so that
    # worker-attributed writes keep their elevated role; per D-block #15.
    op.execute("""
        INSERT INTO users_v7 (username, role, legacy_id, created_at)
        SELECT username, 'admin'::user_role, id, created_at FROM users;
    """)

    # Add UUID-typed staging columns on every FK table; the ``REFERENCES``
    # is intentional — once we copy the rows we keep the constraint.
    # Issued one statement per ``op.execute`` because the asyncpg driver
    # rejects multi-statement prepared statements.
    for tbl in _USER_FK_TABLES:
        op.execute(
            f"ALTER TABLE {tbl} ADD COLUMN created_by_uuid UUID REFERENCES users_v7(id);"
        )
    for tbl in _USER_FK_TABLES:
        op.execute(f"""
            UPDATE {tbl} t
               SET created_by_uuid = u.id
              FROM users_v7 u
             WHERE t.created_by_id = u.legacy_id;
        """)
        op.execute(f"ALTER TABLE {tbl} DROP COLUMN created_by_id;")
        op.execute(f"ALTER TABLE {tbl} RENAME COLUMN created_by_uuid TO created_by_id;")

    # CASCADE drops the v6 users table; no FK from the 6 tables targets it
    # any more (we just dropped the BIGINT columns).
    op.execute("DROP TABLE users CASCADE;")
    op.execute("ALTER TABLE users_v7 RENAME TO users;")
    op.execute("ALTER TABLE users DROP COLUMN legacy_id;")
    # Username + email are nullable but unique-when-set.
    op.execute(
        "CREATE UNIQUE INDEX users_username_uniq ON users(username) "
        "WHERE username IS NOT NULL;"
    )
    op.execute(
        "CREATE UNIQUE INDEX users_email_uniq    ON users(email) "
        "WHERE email IS NOT NULL;"
    )

    # Per-project ACL. ``project_id`` is TEXT because the projects table is
    # not yet modelled (W2.x); composite PK + cascading FK to users keeps
    # rows aligned with their owning user.
    op.execute("""
        CREATE TABLE project_users (
            project_id    TEXT NOT NULL,
            user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            project_role  project_role NOT NULL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (project_id, user_id)
        );
    """)
    op.execute("CREATE INDEX project_users_user_idx ON project_users(user_id);")


def downgrade() -> None:
    """Best-effort reversion to v6 shape.

    Data loss is unavoidable for any user that was created after v7 landed
    (their UUID has no v6 equivalent). Existing legacy rows are preserved
    by re-issuing fresh BIGSERIAL ids.
    """
    # Mirror upgrade in reverse: drop v7-only tables, then shadow ``users``
    # back to BIGSERIAL, rewire, drop ENUMs.
    op.execute("DROP TABLE IF EXISTS project_users;")

    op.execute("""
        CREATE TABLE users_v6 (
            id         BIGSERIAL PRIMARY KEY,
            username   TEXT NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            uuid_id    UUID UNIQUE
        );
    """)
    # Only rows with non-null usernames can be replicated into v6 (which
    # demanded NOT NULL UNIQUE on username). Cognito-only rows are dropped.
    op.execute("""
        INSERT INTO users_v6 (username, created_at, uuid_id)
        SELECT username, created_at, id
          FROM users
         WHERE username IS NOT NULL;
    """)

    for tbl in _USER_FK_TABLES:
        op.execute(f"ALTER TABLE {tbl} ADD COLUMN created_by_bigint BIGINT REFERENCES users_v6(id);")
        op.execute(f"""
            UPDATE {tbl} t
               SET created_by_bigint = u.id
              FROM users_v6 u
             WHERE t.created_by_id = u.uuid_id;
        """)
        op.execute(f"ALTER TABLE {tbl} DROP COLUMN created_by_id;")
        op.execute(f"ALTER TABLE {tbl} RENAME COLUMN created_by_bigint TO created_by_id;")

    op.execute("DROP TABLE users CASCADE;")
    op.execute("ALTER TABLE users_v6 RENAME TO users;")
    op.execute("ALTER TABLE users DROP COLUMN uuid_id;")
    # Match v6 sequence/index/constraint names so a fresh ``upgrade head``
    # does not see the legacy ``users_v6_*`` artifacts.
    op.execute("ALTER SEQUENCE users_v6_id_seq RENAME TO users_id_seq;")
    op.execute("ALTER INDEX users_v6_pkey RENAME TO users_pkey;")
    op.execute("ALTER INDEX users_v6_username_key RENAME TO users_username_key;")

    op.execute("DROP TYPE IF EXISTS project_role;")
    op.execute("DROP TYPE IF EXISTS user_role;")
