"""Tests for SAM manifest generator service."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.services.sam_manifest import generate_sam_manifest_for_scan
from flake_analysis.db.models import Image


@pytest.mark.asyncio
async def test_generate_manifest_maps_sha_to_filename():
    """Test that manifest includes sha256, filename, and grid coords for each image."""
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
    assert len(manifest["images"]) == 2

    # Verify all required fields present
    filenames = {img["filename"] for img in manifest["images"]}
    assert filenames == {"ix001_iy002.png", "ix003_iy004.png"}

    # Verify each image entry has all required fields
    for img in manifest["images"]:
        assert "sha256" in img
        assert "filename" in img
        assert "grid_ix" in img
        assert "grid_iy" in img
        assert len(img["sha256"]) == 64

    # Verify grid coords mapped correctly
    img1 = next(i for i in manifest["images"] if i["filename"] == "ix001_iy002.png")
    assert img1["grid_ix"] == 1
    assert img1["grid_iy"] == 2

    img2 = next(i for i in manifest["images"] if i["filename"] == "ix003_iy004.png")
    assert img2["grid_ix"] == 3
    assert img2["grid_iy"] == 4


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
            s3_uri="s3://test/first.png",
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
            s3_uri="s3://test/second.png",
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
