"""GET /gpu/status — lazy-probe GPU pool snapshot for ComputeTab badge.

Auth: any authenticated user (not admin-only — the badge is rendered in
the user-facing ComputeTab to indicate whether starting a SAM pipeline
will succeed immediately or wait on a spot launch).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.schemas.gpu import GpuPoolStatus
from flake_analysis.api.services.gpu_status import get_gpu_pool_status

router = APIRouter(prefix="/gpu", tags=["gpu"])


@router.get("/status")
async def gpu_status(
    user: Annotated[User, Depends(get_current_user)],
) -> GpuPoolStatus:
    """Return the cached GPU pool status (refreshed at most every 30s)."""
    return await get_gpu_pool_status()
