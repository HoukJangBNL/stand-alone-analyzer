"""W5-B scans router — create scan (W5-B1) + presign/complete/finalize/get (W5-B2)."""
from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_db_session
from flake_analysis.api.schemas.upload import (
    CreateScanRequest,
    PresignRequest,
    PresignResponse,
    ScanResponse,
)
from flake_analysis.api.services import s3_presign, upload_service
from flake_analysis.db.models import Scan
from flake_analysis.db.models.upload import Image, UploadItem

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


@router.post(
    "/scans/{scan_id}/images/presign",
    response_model=PresignResponse,
)
async def presign_image_put(
    scan_id: int,
    req: PresignRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PresignResponse:
    """Issue a presigned PUT URL with SHA256 baked into the signature.

    Pre-checks (scan_id, sha256) and (scan_id, grid_ix, grid_iy) for collisions
    against `images` (already-uploaded) AND active upload_items (in-flight) so
    duplicate work fails fast with 409 before any URL is signed.
    """
    bucket = os.environ.get("SAA_S3_BUCKET")
    prefix = os.environ.get("SAA_S3_PREFIX", "")
    if not bucket:
        raise HTTPException(status_code=500, detail="SAA_S3_BUCKET not configured")

    scan = (await session.execute(
        select(Scan).where(Scan.id == scan_id)
    )).scalar_one_or_none()
    if scan is None:
        raise HTTPException(status_code=404, detail=f"scan {scan_id} not found")

    # 1) sha256 collision with finalized images
    img_dup = (await session.execute(
        select(Image.id)
        .where(Image.scan_id == scan_id)
        .where(Image.sha256 == req.sha256)
    )).scalar_one_or_none()
    if img_dup is not None:
        raise HTTPException(
            status_code=409,
            detail=f"sha256 already uploaded as image {img_dup}",
        )
    # 2) grid collision with finalized images
    grid_dup = (await session.execute(
        select(Image.id)
        .where(Image.scan_id == scan_id)
        .where(Image.grid_ix == req.grid_ix)
        .where(Image.grid_iy == req.grid_iy)
    )).scalar_one_or_none()
    if grid_dup is not None:
        raise HTTPException(
            status_code=409,
            detail=f"grid ({req.grid_ix},{req.grid_iy}) already uploaded as image {grid_dup}",
        )

    upl = await upload_service.get_or_create_upload_session(
        session, scan=scan, created_by_id=user.id,
    )

    # 3) sha256 collision with in-flight upload_item (same session)
    inflight_sha = (await session.execute(
        select(UploadItem.id)
        .where(UploadItem.session_id == upl.id)
        .where(UploadItem.sha256 == req.sha256)
    )).scalar_one_or_none()
    if inflight_sha is not None:
        raise HTTPException(
            status_code=409,
            detail=f"sha256 already in-flight as upload_item {inflight_sha}",
        )
    # 4) grid collision with in-flight upload_item (same session)
    inflight_grid = (await session.execute(
        select(UploadItem.id)
        .where(UploadItem.session_id == upl.id)
        .where(UploadItem.grid_ix == req.grid_ix)
        .where(UploadItem.grid_iy == req.grid_iy)
    )).scalar_one_or_none()
    if inflight_grid is not None:
        raise HTTPException(
            status_code=409,
            detail=f"grid ({req.grid_ix},{req.grid_iy}) already in-flight as upload_item {inflight_grid}",
        )

    key = s3_presign.build_s3_key(
        prefix=prefix, scan_id=scan_id, sha256=req.sha256, filename=req.filename,
    )
    s3_uri = f"s3://{bucket}/{key}"

    try:
        item = await upload_service.create_upload_item(
            session,
            upload_session=upl,
            sha256=req.sha256,
            filename=req.filename,
            size_bytes=req.size_bytes,
            grid_ix=req.grid_ix,
            grid_iy=req.grid_iy,
            s3_uri=s3_uri,
        )
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"upload_item insert conflict: {exc.orig}") from exc

    presigned = s3_presign.presign_put(
        bucket=bucket, key=key, sha256_hex=req.sha256, expires_in=300,
    )
    await session.commit()

    return PresignResponse(
        put_url=presigned["put_url"],
        headers=presigned["headers"],
        upload_item_id=item.id,
        s3_uri=s3_uri,
    )
