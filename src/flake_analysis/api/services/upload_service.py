"""DB write helpers for the W5-B upload flow.

W5-B1 subset: idempotent material insert (on-conflict-do-nothing) + scan
creation. W5-B2 will append upload-session and upload-item lifecycle helpers
to this same file.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.db.models import Material, Scan
from flake_analysis.db.models.upload import (
    UploadItem,
    UploadItemStatus,
    UploadSession,
    UploadSessionStatus,
)


def normalize_material_name(raw: str) -> str:
    """Trim + lowercase. Empty after trim is the caller's problem."""
    return raw.strip().lower()


async def upsert_material(
    session: AsyncSession,
    *,
    name: str,
    created_by_id: UUID | None,
) -> tuple[str, bool]:
    """Insert material idempotently; return (canonical_name, created_flag).

    Uses INSERT ... ON CONFLICT DO NOTHING then SELECT to discover whether
    the row pre-existed. The follow-up SELECT is required because RETURNING
    only fires on an actual insert.
    """
    canonical = normalize_material_name(name)
    if not canonical:
        raise ValueError("material name is empty after normalization")

    stmt = (
        pg_insert(Material)
        .values(name=canonical, created_by_id=created_by_id)
        .on_conflict_do_nothing(index_elements=["name"])
        .returning(Material.name)
    )
    result = await session.execute(stmt)
    inserted = result.scalar_one_or_none()
    await session.flush()
    return canonical, inserted is not None


async def list_materials(session: AsyncSession) -> list[Material]:
    """Return all materials alphabetical by name."""
    result = await session.execute(select(Material).order_by(Material.name))
    return list(result.scalars().all())


async def create_scan(
    session: AsyncSession,
    *,
    name: str,
    material: str,
    image_count: int,
    extra_metadata: dict,
    created_by_id: UUID,
) -> Scan:
    """Create a scan row. Material is auto-added via upsert_material first."""
    canonical, _ = await upsert_material(
        session, name=material, created_by_id=created_by_id,
    )
    scan = Scan(
        name=name,
        material=canonical,
        image_count=image_count,
        extra_metadata=extra_metadata,
        created_by_id=created_by_id,
    )
    session.add(scan)
    await session.flush()
    await session.refresh(scan)
    return scan


async def get_or_create_upload_session(
    session: AsyncSession,
    *,
    scan: Scan,
    created_by_id: UUID,
) -> UploadSession:
    """Per scan, one active upload session. Reuse on subsequent presigns."""
    stmt = (
        select(UploadSession)
        .where(UploadSession.scan_id == scan.id)
        .where(UploadSession.status == UploadSessionStatus.ACTIVE)
        .limit(1)
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    upl = UploadSession(
        scan_id=scan.id,
        total_files=scan.image_count,
        created_by_id=created_by_id,
    )
    session.add(upl)
    await session.flush()
    await session.refresh(upl)
    return upl


async def create_upload_item(
    session: AsyncSession,
    *,
    upload_session: UploadSession,
    sha256: str,
    filename: str,
    size_bytes: int,
    grid_ix: int,
    grid_iy: int,
    s3_uri: str,
) -> UploadItem:
    """Insert a pending upload_item. Uniqueness on (session_id, sha256) is
    enforced by the existing DB constraint — caller catches IntegrityError
    and translates to 409.
    """
    item = UploadItem(
        session_id=upload_session.id,
        sha256=sha256,
        filename=filename,
        size_bytes=size_bytes,
        grid_ix=grid_ix,
        grid_iy=grid_iy,
        s3_uri=s3_uri,
        status=UploadItemStatus.PENDING,
    )
    session.add(item)
    await session.flush()
    await session.refresh(item)
    return item
