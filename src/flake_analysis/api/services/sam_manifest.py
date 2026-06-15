"""SAM manifest generator service.

Generates a JSON manifest mapping sha256 image keys to original filenames + grid
coordinates for the worker S3-sync path. The worker downloads images from S3 by
sha256 key and renames them to their original ix/iy filenames for SAM grid parsing.

Robustness fix (2026-06-15): derive the scan's real S3 prefix from images.s3_uri
(the DB is the source of truth) instead of reconstructing it as scans/{id}/. This
handles scans uploaded under non-empty SAA_S3_PREFIX (e.g. dev/scans/6/).
"""
from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.db.models import Image


def derive_scan_s3_prefix(first_image_uri: str) -> str:
    """Extract the scan's S3 base prefix from an image's s3_uri.

    Given an s3_uri like `s3://bucket/dev/scans/6/images/abc.png`, returns
    `dev/scans/6/` (the base prefix for all scan assets). If the URI is
    `s3://bucket/scans/51/images/xyz.png`, returns `scans/51/` (empty prefix).

    This strips the bucket and `images/<basename>` tail, leaving the directory
    prefix that anchors manifest.json, 07_sam/, etc.

    Args:
        first_image_uri: Full s3:// URI from images.s3_uri

    Returns:
        Scan base prefix with trailing slash (e.g. "scans/51/" or "dev/scans/6/")

    Raises:
        ValueError: If the URI doesn't contain "/images/" or is malformed
    """
    parsed = urlparse(first_image_uri)
    # path is e.g. "/dev/scans/6/images/abc.png" or "/scans/51/images/xyz.png"
    path = parsed.path.lstrip("/")
    if "/images/" not in path:
        raise ValueError(f"s3_uri missing /images/ segment: {first_image_uri}")

    # Split on /images/ and keep everything before it, then add trailing slash
    base = path.split("/images/")[0]
    return f"{base}/"


def s3_key_from_uri(s3_uri: str) -> str:
    """Extract the S3 key from a full s3:// URI (strips scheme + bucket).

    Example: `s3://bucket/dev/scans/6/images/abc.png` -> `dev/scans/6/images/abc.png`

    Args:
        s3_uri: Full s3:// URI

    Returns:
        S3 object key (path without scheme/bucket)
    """
    parsed = urlparse(s3_uri)
    return parsed.path.lstrip("/")


async def generate_sam_manifest_for_scan(
    session: AsyncSession,
    *,
    scan_id: int,
) -> dict:
    """Generate a SAM manifest for a scan's images.

    Returns a dict with version, scan_id, scan_prefix (derived from DB), and
    images list. Each image entry contains sha256, filename, grid coords, and
    the REAL S3 key (from s3_uri) so the worker downloads exactly what the DB
    says exists, not a reconstructed path.

    Args:
        session: Async database session
        scan_id: Scan ID to generate manifest for

    Returns:
        Dict with structure:
        {
            "version": 1,
            "scan_id": scan_id,
            "scan_prefix": "dev/scans/6/" or "scans/51/",
            "images": [
                {
                    "sha256": "abc...",
                    "filename": "ix001_iy002.png",
                    "grid_ix": 1,
                    "grid_iy": 2,
                    "key": "dev/scans/6/images/abc.png"
                },
                ...
            ]
        }

    Raises:
        ValueError: If scan has no images or s3_uri is malformed
    """
    # Query images for the scan, ordered by id for stable ordering
    stmt = (
        select(Image)
        .where(Image.scan_id == scan_id)
        .order_by(Image.id)
    )
    result = await session.execute(stmt)
    images = result.scalars().all()

    if not images:
        # Empty scan: return empty manifest with no scan_prefix (caller handles)
        return {
            "version": 1,
            "scan_id": scan_id,
            "images": [],
        }

    # Derive scan_prefix from first image's s3_uri (source of truth)
    scan_prefix = derive_scan_s3_prefix(images[0].s3_uri)

    # Build manifest with real S3 keys
    return {
        "version": 1,
        "scan_id": scan_id,
        "scan_prefix": scan_prefix,
        "images": [
            {
                "sha256": img.sha256,
                "filename": img.filename,
                "grid_ix": img.grid_ix,
                "grid_iy": img.grid_iy,
                "key": s3_key_from_uri(img.s3_uri),
            }
            for img in images
        ],
    }
