"""P4.2.b: install procrastinate schema in the same DB.

Revision ID: 0006_procrastinate_init
Revises: add_scan_status
Create Date: 2026-05-27

P4.2.b — Phase 4 segmentation web integration.

Procrastinate (https://procrastinate.readthedocs.io/) is a PostgreSQL-backed
job queue. The SAM step in :mod:`flake_analysis.api.routes.run_pipeline`
(and the legacy ``POST /run/sam`` endpoint) defers work to a procrastinate
queue so a GPU worker process can pick it up out-of-band; CPU steps stay
in-process per Phase 4 D5.

We could let users run ``procrastinate schema --apply`` separately, but that
splits the source of truth: alembic owns ``public.*`` for app tables, and
procrastinate's CLI would own its own namespace. Whoever ran ``alembic
upgrade head`` would still need a second command to make the queue usable.

Approach (1) from the P4.2.b brief: have alembic install procrastinate's
schema directly. We read the SQL it ships in ``procrastinate/sql/schema.sql``
via ``importlib.resources`` and execute it verbatim in :func:`upgrade`. This
keeps procrastinate as the single source of truth for its own DDL — we are
not vendoring/copying SQL into the migration file — while still exposing one
command (``alembic upgrade head``) that produces a fully-provisioned DB.

Why a separate psycopg connection?
----------------------------------
The alembic env in this repo is async (``asyncpg`` driver). Procrastinate's
``schema.sql`` is a multi-statement DDL script with dollar-quoted function
bodies. asyncpg refuses multi-statement strings through prepared statements
(``cannot insert multiple commands into a prepared statement``), and that
includes everything routed through SQLAlchemy's ``op.execute()``.

psycopg3 (already a project dependency for sync paths) supports
multi-statement DDL via the simple query protocol. We open a short-lived
sync psycopg connection scoped just to the schema install, run the script,
commit, close. The alembic-managed asyncpg connection is untouched — its
transaction still controls the version-bump row in ``alembic_version``,
so a failure here aborts the migration cleanly.

The downgrade path drops every ``procrastinate_*`` object created by the
upgrade. We hand-roll the DROP statements rather than asking procrastinate
for a teardown SQL (it doesn't ship one) — see :func:`downgrade` for the
full list. Order matters: triggers depend on functions, indexes depend on
tables, and a couple of types are shared between tables. The downgrade DDL
is single-statement-per-call so it can run on the alembic asyncpg connection
just fine via ``op.execute()``.
"""
from __future__ import annotations

from importlib import resources

import psycopg
from alembic import op

from flake_analysis.db.url import get_db_url


revision = "0006_procrastinate_init"
down_revision = "add_scan_status"
branch_labels = None
depends_on = None


def _load_procrastinate_schema_sql() -> str:
    """Return the verbatim contents of ``procrastinate/sql/schema.sql``.

    Sourced from the installed procrastinate package — never copy/paste.
    No %-escaping here: we execute via raw psycopg without parameter
    substitution, so literal ``%`` in the SQL is fine.
    """
    return (resources.files("procrastinate.sql") / "schema.sql").read_text(
        encoding="utf-8"
    )


def _sync_dsn_from_settings() -> str:
    """Build a psycopg-compatible DSN from SAA_DB_* env vars.

    ``get_db_url(async_driver=False)`` returns a SQLAlchemy URL of the form
    ``postgresql+psycopg://user:pass@host:port/db``. psycopg.connect() wants
    a plain ``postgresql://`` DSN; strip the ``+psycopg`` driver tag.
    """
    sa_url = get_db_url(async_driver=False)
    return sa_url.replace("postgresql+psycopg://", "postgresql://", 1)


def upgrade() -> None:
    """Install the procrastinate schema by replaying its packaged DDL.

    Uses a dedicated synchronous psycopg connection because the project's
    async alembic env uses asyncpg, which cannot execute multi-statement
    DDL through its prepared-statement path. See module docstring.
    """
    schema_sql = _load_procrastinate_schema_sql()
    dsn = _sync_dsn_from_settings()
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
        conn.commit()


def downgrade() -> None:
    """Drop every procrastinate_* object created by :func:`upgrade`.

    We list every table, type, and function explicitly so the downgrade is
    deterministic across procrastinate releases — if a future minor version
    adds a new object, the upgrade picks it up automatically (we re-read
    schema.sql), but the downgrade will leave that new object orphaned and
    we'll fix it in a follow-up migration. Worth-the-trade: never silently
    DROP something we didn't create.

    CASCADE is used because triggers + functions form a tangled dep graph
    inside procrastinate's own schema (functions reference enum types,
    triggers reference functions, etc.). CASCADE only crosses procrastinate
    boundaries — no app table references procrastinate objects.

    Each DROP is a single statement, so ``op.execute()`` (which goes through
    the asyncpg-backed alembic connection) is fine — no multi-statement
    issues like the upgrade had.
    """
    # Tables (cascade drops their indexes + triggers + foreign keys)
    for table in (
        "procrastinate_events",
        "procrastinate_periodic_defers",
        "procrastinate_jobs",
        "procrastinate_workers",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    # Functions (cascade drops anything depending on them — should be empty
    # after tables go, but keep CASCADE for safety against future additions)
    for fn in (
        "procrastinate_defer_jobs_v1(procrastinate_job_to_defer_v1[])",
        "procrastinate_defer_periodic_job_v2(character varying, character varying, character varying, character varying, integer, character varying, bigint, jsonb)",
        "procrastinate_fetch_job_v2(character varying[], bigint)",
        "procrastinate_finish_job_v1(bigint, procrastinate_job_status, boolean)",
        "procrastinate_cancel_job_v1(bigint, boolean, boolean)",
        "procrastinate_retry_job_v1(bigint, timestamp with time zone, integer, character varying, character varying)",
        "procrastinate_retry_job_v2(bigint, timestamp with time zone, integer, character varying, character varying)",
        "procrastinate_notify_queue_job_inserted_v1()",
        "procrastinate_notify_queue_abort_job_v1()",
        "procrastinate_trigger_function_status_events_insert_v1()",
        "procrastinate_trigger_function_status_events_update_v1()",
        "procrastinate_trigger_function_scheduled_events_v1()",
        "procrastinate_trigger_abort_requested_events_procedure_v1()",
        "procrastinate_unlink_periodic_defers_v1()",
        "procrastinate_register_worker_v1()",
        "procrastinate_unregister_worker_v1(bigint)",
        "procrastinate_update_heartbeat_v1(bigint)",
        "procrastinate_prune_stalled_workers_v1(double precision)",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {fn} CASCADE")

    # Types last (functions referenced them)
    for ty in (
        "procrastinate_job_to_defer_v1",
        "procrastinate_job_event_type",
        "procrastinate_job_status",
    ):
        op.execute(f"DROP TYPE IF EXISTS {ty} CASCADE")
