"""W5-B scans router — create scan (W5-B1).

W5-B2 appends presign, complete, finalize, and GET handlers to this module.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_db_session
from flake_analysis.api.schemas.upload import (
    CreateScanRequest,
    ScanResponse,
)
from flake_analysis.api.services import upload_service

router = APIRouter(tags=["scans"])


@router.post(
    "/projects/{project_id}/scans",
    status_code=status.HTTP_201_CREATED,
    response_model=ScanResponse,
)
async def create_scan(
    project_id: str,
    req: CreateScanRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ScanResponse:
    """Create a scan under the given project.

    NOTE: project_id is currently routing-only (no scans.project_id FK in v7).
    See "Open follow-up" in the W5-B1 plan for the deferred binding (locked:
    v1 leaves it path-only; v2 introduces a projects table).
    """
    scan = await upload_service.create_scan(
        session,
        name=req.name,
        material=req.material,
        image_count=req.image_count,
        extra_metadata=req.extra_metadata,
        created_by_id=user.id,
    )
    await session.commit()
    return ScanResponse(
        scan_id=scan.id,
        name=scan.name,
        material=scan.material,
        image_count=scan.image_count,
        extra_metadata=scan.extra_metadata,
        created_at=scan.created_at,
    )
