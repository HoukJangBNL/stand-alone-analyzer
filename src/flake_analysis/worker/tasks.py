"""Worker task definitions (P4.2.c).

The single task here, :func:`run_sam`, wraps the in-process SAM step
runner so it can be deferred to a procrastinate queue. The API process
no longer calls :func:`flake_analysis.pipeline.sam.run_sam_step`
directly — it defers a job, and a GPU-resident worker picks it up
via :data:`flake_analysis.worker.app.app`.

Progress fan-out
----------------
Pipeline steps emit ``(progress: float, message: str)`` samples through
a ``progress_callback`` parameter. We bridge those to the API process
via PostgreSQL ``NOTIFY`` on a per-run channel (``sam_progress:{run_id}``).
The API's SSE endpoint LISTENs on that channel and relays each
notification back to the browser as a ``progress`` SSE frame.

The actual emit function (:func:`_emit_progress`) is module-level so
tests can monkeypatch it with a list collector — see
``tests/worker/test_tasks.py``. In production it serializes the payload
as JSON and runs ``NOTIFY`` through a sync psycopg connection.

Wire format
-----------
Each emit takes::

    {
        "type": "progress" | "completed" | "error",
        ... type-specific fields ...
    }

- ``progress``: ``{"progress": float, "message": str}``
- ``completed``: ``{"result": dict}`` (forwarded from the runner)
- ``error``: ``{"code": str, "message": str}`` (exception class + str)

The SSE relay re-shapes these into the existing 5-event vocabulary
(``step_started`` / ``step_progress`` / ``step_completed`` /
``pipeline_done`` / ``pipeline_error``) so the frontend wire format
stays byte-identical.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import psycopg

from flake_analysis.db.url import DbSettings, _require_ssl
from flake_analysis.pipeline.sam import run_sam_step
from flake_analysis.worker.app import app
from flake_analysis.worker.markers import emit_marker

logger = logging.getLogger(__name__)


def _channel_name(run_id: int) -> str:
    """Per-run NOTIFY channel."""
    return f"sam_progress:{run_id}"


def _emit_progress(*, run_id: int, payload: dict[str, Any]) -> None:
    """Emit a progress payload to the API via PG NOTIFY.

    Default implementation opens a short-lived psycopg connection and
    issues ``NOTIFY <channel>, <json>``. Tests monkeypatch this symbol
    with a list collector so no DB is needed.

    Channel name is the same value the API's LISTEN-side helper computes
    from ``run_id`` — see :mod:`flake_analysis.api.sse_listen`.
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

    channel = _channel_name(run_id)
    body = json.dumps(payload, default=str)
    # autocommit so the NOTIFY is delivered immediately (NOTIFY is
    # transactional — without commit it would queue until the
    # transaction completes).
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        with conn.cursor() as cur:
            # psycopg adapts the channel name to a literal identifier;
            # using parameterized NOTIFY via pg_notify() keeps it safe
            # even if a malicious value reached here.
            cur.execute("SELECT pg_notify(%s, %s)", (channel, body))


@app.task(queue="gpu", name="run_sam")
def run_sam(
    *,
    run_id: int,
    raw_images_dir: str,
    analysis_folder: str,
    weights_path: str,
    device: str | None = None,
    model_meta: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run SAM2 inference, fan-out progress + markers, return runner result.

    Marker fan-out: progress messages whose text starts with ``"marker:"``
    are routed to :func:`emit_marker` (worker_events sink) instead of
    SSE NOTIFY. All other progress messages flow through the existing
    SSE path unchanged.

    Lifecycle: emits ``sam_task_start`` at entry (with ``model_meta`` and
    inputs in the payload) and ``sam_task_end`` at exit (with
    ``status``, ``masks_total``, ``errors``, and ``exc`` on failure).
    These let offline analysis derive total wall time without joining
    against ``procrastinate_jobs``.
    """
    emit_marker(
        run_id=run_id,
        event="sam_task_start",
        payload={
            "raw_images_dir": raw_images_dir,
            "analysis_folder": analysis_folder,
            "weights_path": weights_path,
            "model_meta": model_meta,
        },
    )

    def _on_progress(progress: float, message: str) -> None:
        msg = str(message)
        if msg.startswith("marker:"):
            try:
                emit_marker(run_id=run_id, event=msg, payload=None)
            except Exception:  # noqa: BLE001 — never let marker emit failures
                logger.exception("marker emit failed for run_id=%s", run_id)
            return
        try:
            _emit_progress(
                run_id=run_id,
                payload={
                    "type": "progress",
                    "progress": float(progress),
                    "message": msg,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("progress emit failed for run_id=%s", run_id)

    status = "success"
    masks_total = 0
    errors = 0
    try:
        result = run_sam_step(
            raw_images_dir=raw_images_dir,
            analysis_folder=analysis_folder,
            weights_path=weights_path,
            device=device,
            progress_callback=_on_progress,
        )
        masks_total = int(result.get("masks_total", 0) or 0)
        errors = int(result.get("errors", 0) or 0)
    except BaseException as exc:  # noqa: BLE001 — re-raised below
        status = "failed"
        try:
            _emit_progress(
                run_id=run_id,
                payload={
                    "type": "error",
                    "code": type(exc).__name__,
                    "message": str(exc),
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("error emit failed for run_id=%s", run_id)
        emit_marker(
            run_id=run_id,
            event="sam_task_end",
            payload={
                "status": status,
                "masks_total": masks_total,
                "errors": errors,
                "exc": type(exc).__name__,
            },
        )
        raise

    try:
        _emit_progress(
            run_id=run_id,
            payload={"type": "completed", "result": result},
        )
    except Exception:  # noqa: BLE001
        logger.exception("completed emit failed for run_id=%s", run_id)

    emit_marker(
        run_id=run_id,
        event="sam_task_end",
        payload={
            "status": status,
            "masks_total": masks_total,
            "errors": errors,
        },
    )
    return result
