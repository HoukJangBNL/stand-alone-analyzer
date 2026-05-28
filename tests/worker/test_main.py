# tests/worker/test_main.py
"""Smoke tests for ``python -m flake_analysis.worker`` entry-point (P4.2.g).

The worker entry-point parses ``--queue`` (one or more), ``--concurrency``,
and ``--name`` argparse flags then drives :py:meth:`procrastinate.App.run_worker_async`.
SIGTERM/SIGINT handling is delegated to procrastinate (it installs its own
signal handlers when ``install_signal_handlers=True``, which is our default).

These tests:
    1. Check that argparse accepts the expected flags and forwards them to
       ``app.run_worker_async`` with the right shape.
    2. Don't open a real connection — we patch ``app.run_worker_async``
       to a no-op AsyncMock so the entry-point is exercised end-to-end
       without touching the procrastinate pool.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest


@asynccontextmanager
async def _noop_open_async(*_args, **_kwargs):
    """Stand-in for ``app.open_async()`` that skips the real PG pool."""
    yield None


@pytest.mark.asyncio
async def test_main_invokes_run_worker_with_default_queue():
    """Default invocation runs all queues (queues=None)."""
    from flake_analysis.worker.__main__ import _amain

    with patch(
        "flake_analysis.worker.__main__.app.open_async",
        new=_noop_open_async,
    ), patch(
        "flake_analysis.worker.__main__.app.run_worker_async",
        new=AsyncMock(return_value=None),
    ) as mock:
        await _amain([])

    mock.assert_awaited_once()
    kwargs = mock.await_args.kwargs
    assert kwargs.get("queues") is None
    assert kwargs.get("concurrency") == 1


@pytest.mark.asyncio
async def test_main_passes_queue_filter():
    """--queue gpu narrows the worker to a single queue."""
    from flake_analysis.worker.__main__ import _amain

    with patch(
        "flake_analysis.worker.__main__.app.open_async",
        new=_noop_open_async,
    ), patch(
        "flake_analysis.worker.__main__.app.run_worker_async",
        new=AsyncMock(return_value=None),
    ) as mock:
        await _amain(["--queue", "gpu"])

    kwargs = mock.await_args.kwargs
    assert kwargs.get("queues") == ["gpu"]


@pytest.mark.asyncio
async def test_main_supports_multiple_queues_and_concurrency():
    """Multiple --queue values and --concurrency forwarded as a list/int."""
    from flake_analysis.worker.__main__ import _amain

    with patch(
        "flake_analysis.worker.__main__.app.open_async",
        new=_noop_open_async,
    ), patch(
        "flake_analysis.worker.__main__.app.run_worker_async",
        new=AsyncMock(return_value=None),
    ) as mock:
        await _amain(["--queue", "gpu", "--queue", "cpu", "--concurrency", "4"])

    kwargs = mock.await_args.kwargs
    assert kwargs.get("queues") == ["gpu", "cpu"]
    assert kwargs.get("concurrency") == 4


@pytest.mark.asyncio
async def test_main_passes_worker_name():
    """--name is forwarded to procrastinate for log labelling."""
    from flake_analysis.worker.__main__ import _amain

    with patch(
        "flake_analysis.worker.__main__.app.open_async",
        new=_noop_open_async,
    ), patch(
        "flake_analysis.worker.__main__.app.run_worker_async",
        new=AsyncMock(return_value=None),
    ) as mock:
        await _amain(["--name", "saa-gpu-worker-1"])

    kwargs = mock.await_args.kwargs
    assert kwargs.get("name") == "saa-gpu-worker-1"
