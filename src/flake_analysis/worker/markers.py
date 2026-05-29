"""Sync sink for run_sam timing markers and lifecycle events.

Mirrors :func:`flake_analysis.worker.tasks._emit_progress` — opens a
short-lived psycopg connection using ``SAA_DB_*`` env, inserts one
``worker_events`` row, autocommits.

Permanent in production: prod SAM runs also emit these markers, which
means SAM throughput regression analysis works in prod without any
measurement-only code path.

Defensive posture
-----------------
``emit_marker`` never raises. A marker emit failure (DB down, schema
drift, network blip) must not fail an in-flight SAM job. Failures are
logged via ``logger.exception`` and swallowed. Same contract as
``_emit_progress``.

This module deliberately does NOT import from
:mod:`flake_analysis.worker.tasks` — ``tasks`` will later import
``emit_marker`` from here, and a back-edge would cycle.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import psycopg

from flake_analysis.db.url import DbSettings, _require_ssl

logger = logging.getLogger(__name__)


def emit_marker(
    *,
    run_id: int,
    event: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Insert one row into ``worker_events``.

    Never raises — marker emit failures are logged and swallowed so they
    cannot fail an in-flight SAM job. Same defensive posture as
    :func:`flake_analysis.worker.tasks._emit_progress`.

    Args:
        run_id: The same ``run_id`` the deferred ``run_sam`` task got.
        event: Short string like ``"marker:processing_start"`` or
            ``"sam_task_end"``. Goes into the ``event`` column verbatim.
        payload: Optional JSON-serialisable dict; goes into ``payload``
            JSONB. ``None`` writes SQL NULL.
    """
    s = DbSettings()
    conn_kwargs: dict[str, Any] = {
        "host": s.db_host,
        "port": s.db_port,
        "dbname": s.db_name,
    }
    if _require_ssl(s.db_host):
        # RDS rds.force_ssl=1: SSL-only, no prefer→fallback. See #217.
        conn_kwargs["sslmode"] = "require"
    if s.db_user:
        conn_kwargs["user"] = s.db_user
    if s.db_password:
        conn_kwargs["password"] = s.db_password

    payload_json = (
        json.dumps(payload, default=str) if payload is not None else None
    )

    try:
        # autocommit so the INSERT is durable immediately; matches the
        # _emit_progress short-lived-conn pattern in worker/tasks.py.
        with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO worker_events (run_id, event, payload) "
                    "VALUES (%s, %s, %s::jsonb)",
                    (run_id, event, payload_json),
                )
    except Exception:  # noqa: BLE001 — marker emit must never fail SAM
        logger.exception(
            "emit_marker failed: run_id=%s event=%s", run_id, event
        )
