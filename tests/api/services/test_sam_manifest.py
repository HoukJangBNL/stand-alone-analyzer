"""Tests for SAM manifest generator service."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.services.sam_manifest import generate_sam_manifest_for_scan
from flake_analysis.db.models import Image


@pytest.mark.asyncio
async def test_generate_manifest_maps_sha_to_filename():
    """Test that manifest includes sha256, filename, grid coords, and real S3 key for each image."""
    # Mock DB session with 2 image rows
    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [
        Image(
            id=1,
            scan_id=42,
            sha256="a" * 64,
            s3_uri="s3://test-bucket/scans/42/images/aaaa.png",
            filename="ix001_iy002.png",
            width=1024,
            height=1024,
            grid_ix=1,
            grid_iy=2,
        ),
        Image(
            id=2,
            scan_id=42,
            sha256="b" * 64,
            s3_uri="s3://test-bucket/scans/42/images/bbbb.png",
            filename="ix003_iy004.png",
            width=1024,
            height=1024,
            grid_ix=3,
            grid_iy=4,
        ),
    ]
    mock_session.execute = AsyncMock(return_value=mock_result)

    # Generate manifest
    manifest = await generate_sam_manifest_for_scan(mock_session, scan_id=42)

    # Verify structure
    assert manifest["version"] == 1
    assert manifest["scan_id"] == 42
    assert manifest["scan_prefix"] == "scans/42/"
    assert len(manifest["images"]) == 2

    # Verify all required fields present
    filenames = {img["filename"] for img in manifest["images"]}
    assert filenames == {"ix001_iy002.png", "ix003_iy004.png"}

    # Verify each image entry has all required fields including real S3 key
    for img in manifest["images"]:
        assert "sha256" in img
        assert "filename" in img
        assert "grid_ix" in img
        assert "grid_iy" in img
        assert "key" in img
        assert len(img["sha256"]) == 64

    # Verify grid coords mapped correctly
    img1 = next(i for i in manifest["images"] if i["filename"] == "ix001_iy002.png")
    assert img1["grid_ix"] == 1
    assert img1["grid_iy"] == 2
    assert img1["key"] == "scans/42/images/aaaa.png"

    img2 = next(i for i in manifest["images"] if i["filename"] == "ix003_iy004.png")
    assert img2["grid_ix"] == 3
    assert img2["grid_iy"] == 4
    assert img2["key"] == "scans/42/images/bbbb.png"


@pytest.mark.asyncio
async def test_generate_manifest_ordered_by_id():
    """Test that images are ordered by database ID."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [
        Image(
            id=10,
            scan_id=42,
            sha256="first" + "0" * 59,
            s3_uri="s3://test/scans/42/images/first.png",
            filename="ix010_iy010.png",
            width=1024,
            height=1024,
            grid_ix=10,
            grid_iy=10,
        ),
        Image(
            id=20,
            scan_id=42,
            sha256="second" + "0" * 58,
            s3_uri="s3://test/scans/42/images/second.png",
            filename="ix020_iy020.png",
            width=1024,
            height=1024,
            grid_ix=20,
            grid_iy=20,
        ),
    ]
    mock_session.execute = AsyncMock(return_value=mock_result)

    manifest = await generate_sam_manifest_for_scan(mock_session, scan_id=42)

    # Verify order matches database ID order
    assert manifest["images"][0]["filename"] == "ix010_iy010.png"
    assert manifest["images"][1]["filename"] == "ix020_iy020.png"


@pytest.mark.asyncio
async def test_generate_manifest_empty_scan():
    """Test manifest generation for scan with no images."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    manifest = await generate_sam_manifest_for_scan(mock_session, scan_id=99)

    assert manifest["version"] == 1
    assert manifest["scan_id"] == 99
    assert manifest["images"] == []
    # Empty scan has no scan_prefix (no images to derive from)
    assert "scan_prefix" not in manifest


@pytest.mark.asyncio
async def test_generate_manifest_with_prefix():
    """Test that scan_prefix is correctly derived from s3_uri with non-empty prefix."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [
        Image(
            id=1,
            scan_id=6,
            sha256="a" * 64,
            s3_uri="s3://qpress-uploads/dev/scans/6/images/abc.png",
            filename="ix001_iy002.png",
            width=1024,
            height=1024,
            grid_ix=1,
            grid_iy=2,
        ),
    ]
    mock_session.execute = AsyncMock(return_value=mock_result)

    manifest = await generate_sam_manifest_for_scan(mock_session, scan_id=6)

    # Verify scan_prefix is derived from s3_uri (dev/ prefix case)
    assert manifest["scan_prefix"] == "dev/scans/6/"
    assert manifest["images"][0]["key"] == "dev/scans/6/images/abc.png"


@pytest.mark.asyncio
async def test_generate_manifest_empty_prefix():
    """Test that scan_prefix works correctly when SAA_S3_PREFIX was empty at upload."""
    mock_session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [
        Image(
            id=1,
            scan_id=51,
            sha256="x" * 64,
            s3_uri="s3://qpress-uploads/scans/51/images/xyz.png",
            filename="ix010_iy020.png",
            width=1024,
            height=1024,
            grid_ix=10,
            grid_iy=20,
        ),
    ]
    mock_session.execute = AsyncMock(return_value=mock_result)

    manifest = await generate_sam_manifest_for_scan(mock_session, scan_id=51)

    # Verify scan_prefix is "scans/51/" (no dev/ prefix)
    assert manifest["scan_prefix"] == "scans/51/"
    assert manifest["images"][0]["key"] == "scans/51/images/xyz.png"
