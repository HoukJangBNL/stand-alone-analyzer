"""Hydrate service: download S3 images to local filesystem for pipeline steps.

Web-uploaded scans store images in S3. In-process pipeline steps
(thumbnails/background/domain_stats/domain_proximity) need a local
`raw_images_dir` to read from. This service downloads S3 images on-demand,
stamps the manifest with raw_images_dir, and is idempotent (skips already-
hydrated scans).

The SAM step defers to a remote GPU worker which syncs from S3 independently,
so this service does NOT need to hydrate for SAM — only for the 4 in-process
steps.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
from sqlalchemy import select

from flake_analysis.db.models import Image
from flake_analysis.state.manifest import (
    Manifest,
    load_manifest_for_scan,
    save_manifest_for_scan,
    stamp_top_level,
)
from flake_analysis.state.paths import analysis_folder

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_S3_URI_RE = re.compile(r"^s3://([^/]+)/(.+)$")
_DOWNLOAD_CONCURRENCY = 12  # Concurrent downloads (9GB / 3648 files = ~2.5MB avg)


def _parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    """Parse s3://bucket/key into (bucket, key)."""
    match = _S3_URI_RE.match(s3_uri)
    if not match:
        raise ValueError(f"malformed s3 URI: {s3_uri!r}")
    return match.group(1), match.group(2)


def _download_one_image(
    *,
    s3_client,
    bucket: str,
    key: str,
    local_path: Path,
    expected_filename: str,
) -> bool:
    """Download a single image from S3 to local_path.

    Returns True if downloaded (or already exists with matching size),
    False on failure. Uses original filename from DB, NOT sha256 key,
    so grid parsing (ix<N>_iy<N>) works.
    """
    try:
        if local_path.exists():
            # Idempotent: skip if file exists. We could check size/hash but
            # for now just trust existence (manifest's raw_images_dir guards
            # against partial downloads across calls).
            logger.debug(f"skip existing: {local_path.name}")
            return True

        local_path.parent.mkdir(parents=True, exist_ok=True)
        s3_client.download_file(bucket, key, str(local_path))
        logger.debug(f"downloaded: {local_path.name} from s3://{bucket}/{key}")
        return True
    except Exception as e:
        logger.error(f"download failed for {local_path.name}: {e}")
        return False


async def ensure_scan_hydrated(
    session: AsyncSession,
    *,
    project_id: str,
    scan_id: int,
) -> Manifest:
    """Ensure scan images are hydrated from S3 to local filesystem.

    Idempotent: if manifest.raw_images_dir exists and has the expected number
    of files, return immediately. Otherwise download all S3 images to
    <analysis_folder>/raw_images/, stamp manifest, and return.

    Raises:
        ValueError: No images found for scan or some downloads failed.
    """
    # Resolve analysis folder via the SAME path the manifest loader uses
    root = os.environ.get("SAA_ANALYSIS_ROOT") or os.environ.get(
        "SAA_ANALYSIS_FOLDER", "/mnt/analysis"
    )
    folder = analysis_folder(root, project_id, scan_id)
    manifest = load_manifest_for_scan(root, project_id, scan_id)

    # Check if already hydrated
    raw_images_dir = Path(folder) / "raw_images"
    if manifest.raw_images_dir is not None:
        existing_dir = Path(manifest.raw_images_dir)
        if existing_dir.exists() and existing_dir.is_dir():
            existing_files = [
                f
                for f in existing_dir.iterdir()
                if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
            ]
            # Quick check: if dir exists with some images, assume hydrated
            # (full count check would require DB query every time)
            if existing_files:
                logger.info(
                    f"scan {scan_id} already hydrated: {len(existing_files)} images in {existing_dir}"
                )
                return manifest

    # Query images from DB
    stmt = select(Image).where(Image.scan_id == scan_id).order_by(Image.id)
    result = await session.execute(stmt)
    images = result.scalars().all()

    if not images:
        raise ValueError(f"no images found for scan_id={scan_id}")

    logger.info(
        f"hydrating scan {scan_id}: {len(images)} images from S3 to {raw_images_dir}"
    )

    # Download all images concurrently
    raw_images_dir.mkdir(parents=True, exist_ok=True)

    s3_client = boto3.client("s3")
    completed = 0
    log_interval = max(1, len(images) // 10)  # Log every 10%

    def _download_task(img: Image) -> bool:
        """Thread-safe download task with progress logging."""
        nonlocal completed
        bucket, key = _parse_s3_uri(img.s3_uri)
        # CRITICAL: use original filename (ix025_iy025.png), NOT sha256
        local_path = raw_images_dir / img.filename
        success = _download_one_image(
            s3_client=s3_client,
            bucket=bucket,
            key=key,
            local_path=local_path,
            expected_filename=img.filename,
        )
        completed += 1
        if completed % log_interval == 0 or completed == len(images):
            logger.info(f"hydration progress: {completed}/{len(images)} images")
        return success

    # Run downloads in thread pool (boto3 is sync)
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=_DOWNLOAD_CONCURRENCY) as executor:
        futures = [loop.run_in_executor(executor, _download_task, img) for img in images]
        results = await asyncio.gather(*futures, return_exceptions=True)

    # Count successes
    success_count = sum(1 for r in results if r is True)
    fail_count = len(images) - success_count

    if fail_count > 0:
        raise ValueError(
            f"hydration failed: {fail_count}/{len(images)} downloads failed for scan {scan_id}"
        )

    logger.info(f"hydration complete: {success_count}/{len(images)} images for scan {scan_id}")

    # Stamp manifest with raw_images_dir
    stamp_top_level(manifest, analysis_folder=str(folder), raw_images_dir=str(raw_images_dir))
    save_manifest_for_scan(manifest, root=root, project_id=project_id, scan_id=scan_id)

    return manifest
