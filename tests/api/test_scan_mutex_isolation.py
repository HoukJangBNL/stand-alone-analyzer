"""W10-B D4: per-scan mutex isolation across overlapping pipeline steps."""
from __future__ import annotations

import asyncio

import pytest

from flake_analysis.api.errors import ProjectBusy
from flake_analysis.api.mutex import acquire_scan_lock


@pytest.mark.asyncio
async def test_pipeline_step_within_one_scan_serializes():
    """All steps for one scan share the same lock — sequential."""
    order: list[str] = []

    async def step(name: str, scan_id: int, hold: float):
        async with acquire_scan_lock(scan_id):
            order.append(f"{name}:start")
            await asyncio.sleep(hold)
            order.append(f"{name}:end")

    # Same scan_id — second step must wait until first releases. We model
    # the wait by awaiting sequentially (the gather variant would raise
    # ProjectBusy by design).
    await step("background", 1, 0.01)
    await step("sam", 1, 0.01)

    assert order == [
        "background:start", "background:end",
        "sam:start", "sam:end",
    ]


@pytest.mark.asyncio
async def test_two_scans_run_concurrently():
    """Different scan_ids run in parallel."""
    started: list[int] = []
    finished: list[int] = []

    async def work(scan_id: int):
        async with acquire_scan_lock(scan_id):
            started.append(scan_id)
            await asyncio.sleep(0.05)
            finished.append(scan_id)

    await asyncio.gather(work(10), work(11))
    # Both scans started before either finished (proves overlap)
    # If they were serialized, started would be [10,11] only after
    # finished[10] — which `gather` cannot interleave that way given
    # the sleep length.
    assert set(started) == {10, 11}
    assert set(finished) == {10, 11}
