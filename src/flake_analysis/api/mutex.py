"""Per-scan asyncio.Lock registry (W10-B, D4).

Granularity: one lock per `scan_id`. All pipeline steps for a given scan
(thumbnails / background / SAM / domain_stats / selector / clustering /
domain_proximity / explorer) share the lock — full serial within a scan.
Different scans hold separate locks → cross-scan parallel execution is
allowed by design (multi-GPU hosts can chew through two scans at once).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from flake_analysis.api.errors import ProjectBusy

_scan_locks: dict[int, asyncio.Lock] = {}


def _get_lock(scan_id: int) -> asyncio.Lock:
    if scan_id not in _scan_locks:
        _scan_locks[scan_id] = asyncio.Lock()
    return _scan_locks[scan_id]


@asynccontextmanager
async def acquire_scan_lock(scan_id: int):
    """Acquire per-scan lock or raise ProjectBusy immediately if held.

    `ProjectBusy` is reused as the wire-level error to keep the existing
    HTTP 423 envelope identical — clients can't tell whether the lock is
    keyed on project_id or scan_id, and we don't want to break the
    ProjectBusy.code contract just for the rename.
    """
    lock = _get_lock(scan_id)
    if lock.locked():
        raise ProjectBusy(project_id=str(scan_id))

    async with lock:
        yield
