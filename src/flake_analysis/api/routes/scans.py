"""W5-B scans router — create scan (W5-B1) + presign/complete/finalize/get (W5-B2)."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Annotated

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flake_analysis.api import errors as app_errors
from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_db_session
from flake_analysis.api.logging_ctx import get_request_id
from flake_analysis.api.schemas.upload import (
    CompleteRequest,
    CompleteResponse,
    CreateScanRequest,
    FinalizeResponse,
    ImageSummary,
    PresignRequest,
    PresignResponse,
    ScanDetailResponse,
    ScanListResponse,
    ScanResponse,
    ScanSummary,
)
from flake_analysis.api.services import (
    projects_service as projects_svc,
    s3_presign,
    scans_service,
    upload_service,
)
from flake_analysis.api.services.usage import emit as emit_usage
from flake_analysis.db.models import Scan
from flake_analysis.db.models.upload import Image, UploadItem, UploadItemStatus

logger = logging.getLogger(__name__)


def _log_extra(**fields: object) -> dict[str, object]:
    """Build a structured log `extra` dict, dropping fields whose value is None.

    request_id is auto-included when available from the request context.
    """
    rid = get_request_id()
    if rid is not None:
        fields.setdefault("request_id", rid)
    return {k: v for k, v in fields.items() if v is not None}


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
    """Create a scan under the given project (W10-A: scans.project_id FK enforced)."""
    try:
        await projects_svc.get_project(session, project_id=project_id)
    except projects_svc.ProjectNotFound as exc:
        logger.info(
            "create_scan aborted: project not found",
            extra=_log_extra(event="create_scan_project_not_found", project_id=project_id),
        )
        raise app_errors.ProjectNotFound(project_id=project_id) from exc
    scan = await upload_service.create_scan(
        session,
        project_id=project_id,
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


@router.get(
    "/projects/{project_id}/scans",
    response_model=ScanListResponse,
)
async def list_scans_for_project(
    project_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ScanListResponse:
    """List scans belonging to a project, newest first.

    `uploaded_count` is derived via JOIN-count against `images` (never
    stored as a column). `status` is read from `scans.status`.
    """
    uploaded_subq = (
        select(func.count(Image.id))
        .where(Image.scan_id == Scan.id)
        .correlate(Scan)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(Scan, uploaded_subq.label("uploaded_count"))
            .where(Scan.project_id == project_id)
            .order_by(Scan.created_at.desc())
        )
    ).all()
    return ScanListResponse(
        scans=[
            ScanSummary(
                scan_id=scan.id,
                name=scan.name,
                material=scan.material,
                image_count=scan.image_count,
                uploaded_count=int(uploaded_count),
                status=scan.status,
                created_at=scan.created_at,
            )
            for scan, uploaded_count in rows
        ]
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
        logger.error(
            "presign aborted: SAA_S3_BUCKET not configured",
            extra=_log_extra(event="presign_bucket_unconfigured", scan_id=scan_id),
        )
        raise app_errors.S3NotConfigured(scan_id=scan_id)

    try:
        scan = await scans_service.require_editor_for_scan(
            session, scan_id=scan_id, user=user,
        )
    except app_errors.ScanNotFound:
        logger.info(
            "presign aborted: scan not found or no access",
            extra=_log_extra(event="presign_scan_not_found", scan_id=scan_id),
        )
        raise

    # 1) sha256 collision with finalized images
    img_dup = (await session.execute(
        select(Image.id)
        .where(Image.scan_id == scan_id)
        .where(Image.sha256 == req.sha256)
    )).scalar_one_or_none()
    if img_dup is not None:
        logger.info(
            "presign collision (sha256 match)",
            extra=_log_extra(
                event="presign_collision_sha256",
                scan_id=scan_id,
                sha256=req.sha256,
                image_id=img_dup,
            ),
        )
        raise app_errors.PresignCollisionSha256(
            scan_id=scan_id,
            sha256=req.sha256,
            image_id=img_dup,
            in_flight=False,
        )
    # 2) grid collision with finalized images
    grid_dup = (await session.execute(
        select(Image.id)
        .where(Image.scan_id == scan_id)
        .where(Image.grid_ix == req.grid_ix)
        .where(Image.grid_iy == req.grid_iy)
    )).scalar_one_or_none()
    if grid_dup is not None:
        logger.info(
            "presign collision (grid match)",
            extra=_log_extra(
                event="presign_collision_grid",
                scan_id=scan_id,
                grid_ix=req.grid_ix,
                grid_iy=req.grid_iy,
                image_id=grid_dup,
            ),
        )
        raise app_errors.PresignCollisionGrid(
            scan_id=scan_id,
            grid_ix=req.grid_ix,
            grid_iy=req.grid_iy,
            image_id=grid_dup,
            in_flight=False,
        )

    upl = await upload_service.get_or_create_upload_session(
        session, scan=scan, created_by_id=user.id,
    )

    # 3) sha256 collision with in-flight upload_item (same session).
    # If the existing row's (filename, grid_ix, grid_iy, size_bytes) all match
    # the incoming request, treat as an idempotent replay: regenerate the
    # presigned URL for the same object key and return 200 with the same
    # upload_item_id. Only 409 when fields actually disagree.
    inflight_item = (await session.execute(
        select(UploadItem)
        .where(UploadItem.session_id == upl.id)
        .where(UploadItem.sha256 == req.sha256)
    )).scalar_one_or_none()
    if inflight_item is not None:
        fields_match = (
            inflight_item.filename == req.filename
            and inflight_item.grid_ix == req.grid_ix
            and inflight_item.grid_iy == req.grid_iy
            and inflight_item.size_bytes == req.size_bytes
        )
        if fields_match:
            existing_uri = inflight_item.s3_uri or ""
            bucket_prefix = f"s3://{bucket}/"
            if not existing_uri.startswith(bucket_prefix):
                logger.error(
                    "presign idempotent replay: stored s3_uri bucket mismatch",
                    extra=_log_extra(
                        event="presign_idempotent_bucket_mismatch",
                        scan_id=scan_id,
                        sha256=req.sha256,
                        upload_item_id=inflight_item.id,
                        s3_uri=existing_uri,
                    ),
                )
                raise app_errors.PresignIdempotentBucketMismatch(
                    scan_id=scan_id,
                    upload_item_id=inflight_item.id,
                )
            existing_key = existing_uri[len(bucket_prefix):]
            presigned = s3_presign.presign_put(
                bucket=bucket,
                key=existing_key,
                sha256_hex=req.sha256,
                expires_in=s3_presign.PRESIGN_TTL_SECONDS,
            )
            logger.info(
                "presign idempotent replay",
                extra=_log_extra(
                    event="presign_idempotent_replay",
                    scan_id=scan_id,
                    sha256=req.sha256,
                    upload_item_id=inflight_item.id,
                ),
            )
            return PresignResponse(
                put_url=presigned["put_url"],
                headers=presigned["headers"],
                upload_item_id=inflight_item.id,
                s3_uri=existing_uri,
            )
        logger.info(
            "presign collision (sha256 match)",
            extra=_log_extra(
                event="presign_collision_sha256",
                scan_id=scan_id,
                sha256=req.sha256,
                upload_item_id=inflight_item.id,
                in_flight=True,
            ),
        )
        raise app_errors.PresignCollisionSha256(
            scan_id=scan_id,
            sha256=req.sha256,
            upload_item_id=inflight_item.id,
            in_flight=True,
        )
    # 4) grid collision with in-flight upload_item (same session)
    inflight_grid = (await session.execute(
        select(UploadItem.id)
        .where(UploadItem.session_id == upl.id)
        .where(UploadItem.grid_ix == req.grid_ix)
        .where(UploadItem.grid_iy == req.grid_iy)
    )).scalar_one_or_none()
    if inflight_grid is not None:
        logger.info(
            "presign collision (grid match)",
            extra=_log_extra(
                event="presign_collision_grid",
                scan_id=scan_id,
                grid_ix=req.grid_ix,
                grid_iy=req.grid_iy,
                upload_item_id=inflight_grid,
                in_flight=True,
            ),
        )
        raise app_errors.PresignCollisionGrid(
            scan_id=scan_id,
            grid_ix=req.grid_ix,
            grid_iy=req.grid_iy,
            upload_item_id=inflight_grid,
            in_flight=True,
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
        logger.info(
            "presign upload_item insert conflict",
            extra=_log_extra(
                event="presign_upload_item_conflict",
                scan_id=scan_id,
                sha256=req.sha256,
                grid_ix=req.grid_ix,
                grid_iy=req.grid_iy,
            ),
        )
        raise app_errors.PresignUploadItemConflict(
            scan_id=scan_id,
            sha256=req.sha256,
            grid_ix=req.grid_ix,
            grid_iy=req.grid_iy,
            db_error=str(exc.orig),
        ) from exc

    presigned = s3_presign.presign_put(
        bucket=bucket,
        key=key,
        sha256_hex=req.sha256,
        expires_in=s3_presign.PRESIGN_TTL_SECONDS,
    )
    await session.commit()

    return PresignResponse(
        put_url=presigned["put_url"],
        headers=presigned["headers"],
        upload_item_id=item.id,
        s3_uri=s3_uri,
    )


@router.post(
    "/scans/{scan_id}/images/{upload_item_id}/complete",
    response_model=CompleteResponse,
)
async def complete_image(
    scan_id: int,
    upload_item_id: int,
    req: CompleteRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CompleteResponse:
    """Promote a pending upload_item to a canonical images row.

    Idempotent: if the upload_item is already UPLOADED with image_id set,
    return that image_id without re-running head_object or re-inserting.
    """
    bucket = os.environ.get("SAA_S3_BUCKET")
    if not bucket:
        logger.error(
            "complete aborted: SAA_S3_BUCKET not configured",
            extra=_log_extra(
                event="complete_bucket_unconfigured",
                scan_id=scan_id,
                upload_item_id=upload_item_id,
            ),
        )
        raise app_errors.S3NotConfigured(
            scan_id=scan_id,
            upload_item_id=upload_item_id,
        )

    try:
        await scans_service.require_editor_for_scan(
            session, scan_id=scan_id, user=user,
        )
    except app_errors.ScanNotFound:
        logger.info(
            "complete aborted: scan not found or no access",
            extra=_log_extra(
                event="complete_scan_not_found",
                scan_id=scan_id,
                upload_item_id=upload_item_id,
            ),
        )
        raise

    item = (await session.execute(
        select(UploadItem)
        .options(selectinload(UploadItem.session))
        .where(UploadItem.id == upload_item_id)
    )).scalar_one_or_none()
    if item is None:
        logger.info(
            "complete aborted: upload_item not found",
            extra=_log_extra(
                event="complete_upload_item_not_found",
                scan_id=scan_id,
                upload_item_id=upload_item_id,
            ),
        )
        raise app_errors.UploadItemNotFound(
            scan_id=scan_id,
            upload_item_id=upload_item_id,
        )
    if item.session.scan_id != scan_id:
        logger.info(
            "complete aborted: upload_item belongs to different scan",
            extra=_log_extra(
                event="complete_upload_item_scan_mismatch",
                scan_id=scan_id,
                upload_item_id=upload_item_id,
                actual_scan_id=item.session.scan_id,
            ),
        )
        raise app_errors.UploadItemScanMismatch(
            scan_id=scan_id,
            upload_item_id=upload_item_id,
            actual_scan_id=item.session.scan_id,
        )

    # Idempotency short-circuit
    if item.status == UploadItemStatus.UPLOADED and item.image_id is not None:
        return CompleteResponse(image_id=item.image_id)

    # Verify the S3 object exists
    if item.s3_uri is None or not item.s3_uri.startswith(f"s3://{bucket}/"):
        logger.info(
            "complete aborted: invalid s3_uri",
            extra=_log_extra(
                event="complete_invalid_s3_uri",
                scan_id=scan_id,
                upload_item_id=upload_item_id,
            ),
        )
        raise app_errors.CompleteInvalidS3Uri(
            scan_id=scan_id,
            upload_item_id=upload_item_id,
        )
    key = item.s3_uri[len(f"s3://{bucket}/"):]
    try:
        # B3: head_object is a synchronous boto3 call (~50-200ms with real S3).
        # Dispatch it to the default threadpool so concurrent complete-calls
        # don't serialize on the asyncio event loop.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, lambda: s3_presign.head_object(bucket=bucket, key=key),
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            logger.info(
                "complete aborted: S3 object missing",
                extra=_log_extra(
                    event="complete_s3_object_missing",
                    scan_id=scan_id,
                    upload_item_id=upload_item_id,
                    s3_key=key,
                ),
            )
            raise app_errors.CompleteS3ObjectMissing(
                scan_id=scan_id,
                upload_item_id=upload_item_id,
                s3_key=key,
            ) from exc
        logger.exception(
            "complete failed: S3 head_object error",
            extra=_log_extra(
                event="complete_s3_head_error",
                scan_id=scan_id,
                upload_item_id=upload_item_id,
                s3_key=key,
                s3_error_code=code,
            ),
        )
        raise app_errors.CompleteS3HeadError(
            scan_id=scan_id,
            upload_item_id=upload_item_id,
            s3_key=key,
            s3_error_code=code,
        ) from exc

    # Insert canonical Image row
    image = Image(
        scan_id=scan_id,
        sha256=item.sha256,
        s3_uri=item.s3_uri,
        width=req.width,
        height=req.height,
        filename=item.filename,
        grid_ix=item.grid_ix if item.grid_ix is not None else 0,
        grid_iy=item.grid_iy if item.grid_iy is not None else 0,
    )
    session.add(image)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        logger.info(
            "complete failed: image insert conflict",
            extra=_log_extra(
                event="complete_image_conflict",
                scan_id=scan_id,
                upload_item_id=upload_item_id,
                sha256=item.sha256,
            ),
        )
        raise app_errors.CompleteImageConflict(
            scan_id=scan_id,
            upload_item_id=upload_item_id,
            sha256=item.sha256,
            db_error=str(exc.orig),
        ) from exc

    item.status = UploadItemStatus.UPLOADED
    item.image_id = image.id
    await session.commit()
    return CompleteResponse(image_id=image.id)


@router.post(
    "/scans/{scan_id}/finalize",
    response_model=FinalizeResponse,
)
async def finalize_scan(
    scan_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FinalizeResponse:
    scan = (await session.execute(
        select(Scan).where(Scan.id == scan_id)
    )).scalar_one_or_none()
    if scan is None:
        logger.info(
            "finalize aborted: scan not found",
            extra=_log_extra(event="finalize_scan_not_found", scan_id=scan_id),
        )
        raise app_errors.ScanNotFound(scan_id=scan_id)

    uploaded = (await session.execute(
        select(func.count(Image.id)).where(Image.scan_id == scan_id)
    )).scalar_one()
    missing = max(scan.image_count - int(uploaded), 0)
    if missing > 0:
        logger.info(
            "finalize aborted: incomplete upload",
            extra=_log_extra(
                event="finalize_incomplete",
                scan_id=scan_id,
                missing=missing,
                uploaded=int(uploaded),
                expected=scan.image_count,
            ),
        )
        raise app_errors.FinalizeIncomplete(
            scan_id=scan_id,
            status="incomplete",
            missing=missing,
            uploaded=int(uploaded),
            expected=scan.image_count,
        )

    scan.status = "ready"
    await emit_usage(session, user, "scan_uploaded", {"scan_id": scan_id})
    await session.commit()
    return FinalizeResponse(status="ready", missing=0)


@router.get(
    "/scans/{scan_id}",
    response_model=ScanDetailResponse,
)
async def get_scan(
    scan_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ScanDetailResponse:
    scan = (await session.execute(
        select(Scan).where(Scan.id == scan_id)
    )).scalar_one_or_none()
    if scan is None:
        logger.info(
            "get_scan aborted: scan not found",
            extra=_log_extra(event="get_scan_not_found", scan_id=scan_id),
        )
        raise app_errors.ScanNotFound(scan_id=scan_id)

    images = (await session.execute(
        select(Image).where(Image.scan_id == scan_id).order_by(Image.id)
    )).scalars().all()
    if images:
        ix_vals = [im.grid_ix for im in images]
        iy_vals = [im.grid_iy for im in images]
        ix_range: tuple[int, int] | None = (min(ix_vals), max(ix_vals))
        iy_range: tuple[int, int] | None = (min(iy_vals), max(iy_vals))
    else:
        ix_range = None
        iy_range = None

    return ScanDetailResponse(
        scan_id=scan.id,
        name=scan.name,
        material=scan.material,
        image_count=scan.image_count,
        extra_metadata=scan.extra_metadata,
        uploaded_count=len(images),
        grid_ix_range=ix_range,
        grid_iy_range=iy_range,
        images=[
            ImageSummary(
                image_id=im.id, grid_ix=im.grid_ix, grid_iy=im.grid_iy,
                s3_uri=im.s3_uri, sha256=im.sha256,
            )
            for im in images
        ],
    )
