"""SAM manifest generator service.

Generates a JSON manifest mapping sha256 image keys to original filenames + grid
coordinates for the worker S3-sync path. The worker downloads images from S3 by
sha256 key and renames them to their original ix/iy filenames for SAM grid parsing.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.db.models import Image


async def generate_sam_manifest_for_scan(
    session: AsyncSession,
    *,
    scan_id: int,
) -> dict:
    """Generate a SAM manifest for a scan's images.

    Returns a dict with version, scan_id, and images list. Each image entry
    contains sha256, filename, grid_ix, and grid_iy for worker S3-sync renaming.

    Args:
        session: Async database session
        scan_id: Scan ID to generate manifest for

    Returns:
        Dict with structure:
        {
            "version": 1,
            "scan_id": scan_id,
            "images": [
                {
                    "sha256": "abc...",
                    "filename": "ix001_iy002.png",
                    "grid_ix": 1,
                    "grid_iy": 2
                },
                ...
            ]
        }
    """
    # Query images for the scan, ordered by id for stable ordering
    stmt = (
        select(Image)
        .where(Image.scan_id == scan_id)
        .order_by(Image.id)
    )
    result = await session.execute(stmt)
    images = result.scalars().all()

    # Build manifest
    return {
        "version": 1,
        "scan_id": scan_id,
        "images": [
            {
                "sha256": img.sha256,
                "filename": img.filename,
                "grid_ix": img.grid_ix,
                "grid_iy": img.grid_iy,
            }
            for img in images
        ],
    }
