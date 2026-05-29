"""emit_marker writes a single worker_events row via sync psycopg.

Note on transactions: ``pg_session`` wraps the test in a SAVEPOINT-style
transaction that is rolled back at teardown (see tests/db/conftest.py).
But ``emit_marker`` opens its OWN psycopg connection with ``autocommit=True``
— that write happens OUTSIDE the test's transaction. So:

* The row IS visible to ``pg_session`` (both connect to the same DB via
  ``SAA_DB_*`` / ``SAA_TEST_DATABASE_URL``), even before the outer tx
  commits, because autocommit makes the INSERT immediately visible to
  any other connection at READ COMMITTED.
* The row WILL persist after the test runs (autocommit write is not part
  of the rollback).

We therefore wrap each test in ``try/finally`` and DELETE the inserted
rows via a sibling autocommit connection. Mirrors the
``_psycopg_kwargs`` helper used in tests/api/test_sse_listen.py.
"""
from __future__ import annotations

from typing import Any

import psycopg
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.db.models import WorkerEvent
from flake_analysis.db.url import DbSettings, _require_ssl
from flake_analysis.worker.markers import emit_marker

pytestmark = pytest.mark.pg


def _psycopg_kwargs() -> dict[str, Any]:
    """Build psycopg.connect kwargs from SAA_DB_* env (mirrors emit_marker)."""
    s = DbSettings()
    kw: dict[str, Any] = {
        "host": s.db_host,
        "port": s.db_port,
        "dbname": s.db_name,
    }
    if _require_ssl(s.db_host):
        kw["sslmode"] = "require"
    if s.db_user:
        kw["user"] = s.db_user
    if s.db_password:
        kw["password"] = s.db_password
    return kw


def _delete_run_rows(run_id: int) -> None:
    """Delete worker_events rows for run_id via a sibling autocommit conn."""
    with psycopg.connect(**_psycopg_kwargs(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM worker_events WHERE run_id = %s", (run_id,))


@pytest.mark.asyncio
async def test_emit_marker_round_trip(pg_session: AsyncSession) -> None:
    """emit_marker inserts one row visible to a separate session."""
    run_id = 99
    # Pre-clean in case a prior failed run leaked rows.
    _delete_run_rows(run_id)
    try:
        emit_marker(
            run_id=run_id,
            event="marker:processing_start",
            payload={"n_gpus": 8},
        )

        rows = (
            await pg_session.execute(
                select(WorkerEvent).where(WorkerEvent.run_id == run_id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].event == "marker:processing_start"
        assert rows[0].payload == {"n_gpus": 8}
    finally:
        _delete_run_rows(run_id)


@pytest.mark.asyncio
async def test_emit_marker_optional_payload(pg_session: AsyncSession) -> None:
    """emit_marker accepts payload=None and stores SQL NULL in payload."""
    run_id = 100
    _delete_run_rows(run_id)
    try:
        emit_marker(
            run_id=run_id,
            event="marker:model_load_start",
            payload=None,
        )

        rows = (
            await pg_session.execute(
                select(WorkerEvent).where(WorkerEvent.run_id == run_id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].payload is None
    finally:
        _delete_run_rows(run_id)
