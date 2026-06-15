"""Tests for hydrate service (S3 image download for pipeline)."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.services.hydrate import ensure_scan_hydrated
from flake_analysis.db.models import Image


@pytest.mark.asyncio
async def test_ensure_scan_hydrated_downloads_images(monkeypatch):
    """Test that hydrate service downloads S3 images to local filesystem."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Mock environment
        monkeypatch.setenv("SAA_ANALYSIS_ROOT", tmpdir)

        # Mock DB session with image rows
        mock_session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [
            Image(
                id=1,
                scan_id=42,
                sha256="a" * 64,
                s3_uri="s3://test-bucket/scans/42/images/aaaa.png",
                filename="ix001_iy001.png",
                width=1024,
                height=1024,
                grid_ix=1,
                grid_iy=1,
            ),
            Image(
                id=2,
                scan_id=42,
                sha256="b" * 64,
                s3_uri="s3://test-bucket/scans/42/images/bbbb.png",
                filename="ix002_iy002.png",
                width=1024,
                height=1024,
                grid_ix=2,
                grid_iy=2,
            ),
        ]
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Mock boto3 S3 client
        mock_s3_client = MagicMock()
        mock_s3_client.download_file = MagicMock()

        with patch("boto3.client", return_value=mock_s3_client):
            # Run hydration
            manifest = await ensure_scan_hydrated(
                mock_session,
                project_id="test-project",
                scan_id=42,
            )

        # Verify manifest has raw_images_dir set
        assert manifest.raw_images_dir is not None
        raw_dir = Path(manifest.raw_images_dir)
        assert raw_dir.exists()
        assert "raw_images" in str(raw_dir)

        # Verify S3 downloads were called with correct filenames
        assert mock_s3_client.download_file.call_count == 2
        calls = mock_s3_client.download_file.call_args_list

        # Check that downloads used ORIGINAL filenames (ix/iy), not sha256
        # download_file signature: (bucket, key, local_path)
        downloaded_files = [call[0][2] for call in calls]  # 3rd positional arg
        assert any("ix001_iy001.png" in str(f) for f in downloaded_files)
        assert any("ix002_iy002.png" in str(f) for f in downloaded_files)


@pytest.mark.asyncio
async def test_ensure_scan_hydrated_idempotent(monkeypatch):
    """Test that hydrate service is idempotent (skips already-hydrated scans)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("SAA_ANALYSIS_ROOT", tmpdir)

        # Pre-create raw_images dir with a file to simulate already-hydrated
        raw_dir = Path(tmpdir) / "test-project" / "42" / "raw_images"
        raw_dir.mkdir(parents=True)
        (raw_dir / "ix001_iy001.png").write_text("fake image")

        # Pre-create manifest with raw_images_dir set
        manifest_file = Path(tmpdir) / "test-project" / "42" / "manifest.json"
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.write_text(
            f'{{"version": 1, "raw_images_dir": "{raw_dir}"}}'
        )

        mock_session = AsyncMock(spec=AsyncSession)
        mock_s3_client = MagicMock()

        with patch("boto3.client", return_value=mock_s3_client):
            manifest = await ensure_scan_hydrated(
                mock_session,
                project_id="test-project",
                scan_id=42,
            )

        # Verify no S3 downloads were called (already hydrated)
        assert mock_s3_client.download_file.call_count == 0

        # Verify manifest still has raw_images_dir
        assert manifest.raw_images_dir == str(raw_dir)
