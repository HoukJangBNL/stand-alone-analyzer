"""W10-B: per-scan lock semantics."""
from __future__ import annotations

import asyncio

import pytest

from flake_analysis.api.errors import ProjectBusy  # error class kept for now
from flake_analysis.api.mutex import acquire_scan_lock


@pytest.mark.asyncio
async def test_same_scan_serializes():
    """Two `acquire_scan_lock(7)` simultaneously: second raises ProjectBusy (immediate fail-fast)."""
    async with acquire_scan_lock(7):
        with pytest.raises(ProjectBusy):
            async with acquire_scan_lock(7):
                pass


@pytest.mark.asyncio
async def test_different_scans_parallel_ok():
    """`acquire_scan_lock(7)` does not block `acquire_scan_lock(8)`."""
    async with acquire_scan_lock(7):
        async with acquire_scan_lock(8):
            pass  # no exception
