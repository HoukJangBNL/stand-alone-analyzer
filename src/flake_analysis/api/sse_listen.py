"""PG NOTIFY listener for SSE fan-out (P4.2.e).

The procrastinate worker emits progress samples via ``pg_notify`` on a
per-run channel (``sam_progress:{run_id}``). The API process LISTENs on
that channel and relays each payload back to the browser as an SSE
frame.

This module owns the LISTEN side. Callers iterate
:func:`listen_for_run`; each iteration yields a decoded JSON payload.
The async generator owns a dedicated asyncpg connection — LISTEN holds
the connection for the lifetime of the subscription, so we cannot reuse
the SQLAlchemy pool.

Cancellation contract
---------------------
The caller is expected to break out of the iteration when it observes
a terminal payload (``completed`` or ``error``). The generator releases
the asyncpg connection in its ``finally`` block, so cancelling the
consumer task or breaking out of the ``async for`` both clean up.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import asyncpg

from flake_analysis.db.url import DbSettings, _require_ssl

logger = logging.getLogger(__name__)


def channel_name(run_id: int) -> str:
    """Per-run NOTIFY channel — must match the worker's emit side."""
    return f"sam_progress:{run_id}"


def _asyncpg_kwargs() -> dict[str, Any]:
    """Build asyncpg.connect kwargs from SAA_DB_* env vars.

    asyncpg uses ``database=`` (not ``dbname=``) and ``ssl=`` (not
    ``sslmode=``) — those are the argname differences from psycopg.
    SSL is forced to ``require`` to match RDS ``rds.force_ssl=1`` and
    suppress libpq-style ``prefer→fallback`` retry paths
    (Refs: #211, #217).
    """
    s = DbSettings()
    kw: dict[str, Any] = {
        "host": s.db_host,
        "port": s.db_port,
        "database": s.db_name,
    }
    if _require_ssl(s.db_host):
        # SSL forced on RDS (rds.force_ssl=1). Local dev/test PGs typically
        # lack SSL, so skip the gate there. Refs: #211, #217.
        kw["ssl"] = "require"
    if s.db_user:
        kw["user"] = s.db_user
    if s.db_password:
        kw["password"] = s.db_password
    return kw


async def listen_for_run(run_id: int) -> AsyncIterator[dict[str, Any]]:
    """Yield decoded JSON payloads received on ``sam_progress:{run_id}``.

    Opens a fresh asyncpg connection, registers a notification listener,
    and yields each payload as a dict. Closes the connection on exit
    (clean break, generator close, or task cancel).

    The listener uses asyncpg's ``add_listener`` callback into a local
    ``asyncio.Queue`` so the consumer can ``await`` for the next item
    without polling.
    """
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def _on_notification(_conn, _pid, _channel, payload: str) -> None:
        # asyncpg invokes this callback on the connection's event loop;
        # put_nowait is safe (no concurrent producers from other threads)
        # and we don't want to block the protocol reader.
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning(
                "non-JSON payload on %s: %r", channel_name(run_id), payload
            )
            return
        queue.put_nowait(decoded)

    channel = channel_name(run_id)
    conn = await asyncpg.connect(**_asyncpg_kwargs())
    try:
        await conn.add_listener(channel, _on_notification)
        try:
            while True:
                payload = await queue.get()
                yield payload
        finally:
            # remove_listener may itself raise if the connection is
            # already gone; we don't care — we're tearing down.
            try:
                await conn.remove_listener(channel, _on_notification)
            except Exception:  # noqa: BLE001
                logger.debug(
                    "remove_listener failed for %s (already closed?)", channel
                )
    finally:
        await conn.close()
