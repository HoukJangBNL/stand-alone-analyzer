"""P4.2.e — PG NOTIFY listener for SSE fan-out.

The API process LISTENs on a per-run channel that the worker NOTIFYs
into. We need a small helper that:

1. Opens a dedicated asyncpg connection (LISTEN holds the connection
   for the lifetime of the subscription, so we can't reuse the
   SQLAlchemy pool).
2. Decodes JSON payloads off the channel.
3. Yields them as an async iterator.
4. Stops when the caller breaks out, releases the connection cleanly.

These tests use the real saa_test DB (``@pytest.mark.pg``) because
asyncpg's LISTEN/NOTIFY can't be in-memory. We pump notifications from
a sibling psycopg connection (mirroring the production worker's emit
path) and assert the listener picks them up.
"""
from __future__ import annotations

import asyncio
import json

import psycopg
import pytest

from flake_analysis.db.url import DbSettings


def _psycopg_kwargs() -> dict:
    s = DbSettings()
    kw = {"host": s.db_host, "port": s.db_port, "dbname": s.db_name}
    if s.db_user:
        kw["user"] = s.db_user
    if s.db_password:
        kw["password"] = s.db_password
    return kw


def _emit_sync(channel: str, payload: dict) -> None:
    """Mirror flake_analysis.worker.tasks._emit_progress without the run_id wrapper."""
    body = json.dumps(payload, default=str)
    with psycopg.connect(**_psycopg_kwargs(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_notify(%s, %s)", (channel, body))


@pytest.mark.pg
@pytest.mark.asyncio
async def test_listen_yields_decoded_notifications():
    """Notifications sent to the listened channel arrive decoded as JSON."""
    from flake_analysis.api.sse_listen import listen_for_run

    run_id = 12345

    received: list[dict] = []

    async def consumer():
        async for payload in listen_for_run(run_id):
            received.append(payload)
            if payload.get("type") == "completed":
                break

    task = asyncio.create_task(consumer())

    # Give the listener a moment to subscribe before notifying.
    await asyncio.sleep(0.1)

    channel = f"sam_progress:{run_id}"
    _emit_sync(channel, {"type": "progress", "progress": 0.25, "message": "quarter"})
    _emit_sync(channel, {"type": "progress", "progress": 0.75, "message": "almost"})
    _emit_sync(channel, {"type": "completed", "result": {"images": 2}})

    await asyncio.wait_for(task, timeout=5.0)

    assert len(received) == 3
    assert received[0] == {"type": "progress", "progress": 0.25, "message": "quarter"}
    assert received[2]["type"] == "completed"
    assert received[2]["result"] == {"images": 2}


@pytest.mark.pg
@pytest.mark.asyncio
async def test_listen_filters_by_run_id():
    """Notifications for a different run_id don't bleed into our listener."""
    from flake_analysis.api.sse_listen import listen_for_run

    target_run_id = 99
    other_run_id = 100

    received: list[dict] = []

    async def consumer():
        async for payload in listen_for_run(target_run_id):
            received.append(payload)
            if payload.get("type") == "completed":
                break

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.1)

    # Noise on a sibling channel — must NOT reach the target listener.
    _emit_sync(f"sam_progress:{other_run_id}", {"type": "progress", "progress": 0.5, "message": "noise"})
    # Real signal on the target channel.
    _emit_sync(f"sam_progress:{target_run_id}", {"type": "completed", "result": {"ok": True}})

    await asyncio.wait_for(task, timeout=5.0)

    assert len(received) == 1
    assert received[0]["type"] == "completed"


@pytest.mark.pg
@pytest.mark.asyncio
async def test_listen_releases_on_cancel():
    """Cancelling the consumer breaks out cleanly without leaking the connection."""
    from flake_analysis.api.sse_listen import listen_for_run

    run_id = 200

    async def consumer():
        async for _ in listen_for_run(run_id):
            pass

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # If we reach here without hanging, the helper handled cancellation.


# ---------------------------------------------------------------------------
# Regression test for run-51 hang (2026-06-16)
# ---------------------------------------------------------------------------


def test_asyncpg_kwargs_has_timeout(monkeypatch):
    """Regression guard for run-51 hang: _asyncpg_kwargs() must include
    timeout (asyncpg's connect-timeout arg) to prevent infinite block
    when the bastion tunnel stalls. Mirrored fix for the launcher's
    PgAdvisoryLock._conn_kwargs() connect_timeout."""
    from flake_analysis.api.sse_listen import _asyncpg_kwargs

    # Set minimal SAA_DB_* env so DbSettings() constructs
    monkeypatch.setenv("SAA_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("SAA_DB_PORT", "5433")
    monkeypatch.setenv("SAA_DB_NAME", "qpress")
    monkeypatch.setenv("SAA_DB_USER", "houk")

    kwargs = _asyncpg_kwargs()
    assert "timeout" in kwargs
    assert isinstance(kwargs["timeout"], (int, float))
    assert kwargs["timeout"] > 0
