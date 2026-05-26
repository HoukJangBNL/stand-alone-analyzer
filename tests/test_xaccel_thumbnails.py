"""Plan 5 Task 7 — verify the thumbnail static route uses X-Accel-Redirect.

Plan 4 Task 13 returned a FileResponse (Option A). Plan 5 converts the
route per deployment-design §2.2 Option B: when the project's
00_thumbnails/index.json carries a cache_dir, the response body MUST be
empty and the X-Accel-Redirect header MUST point at
/_tiles_internal/<sha>/lod{N}/<stem>.webp.

Legacy projects without cache_dir keep the FileResponse fallback so
existing analysis folders still work — explicitly tested below.
"""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


def _seed_local_cache_layout(tmp_path: Path, sha: str = "deadbeef12345678") -> Path:
    """Mimic the redirected layout where tiles live under cache_dir/."""
    folder = tmp_path / "analysis"
    folder.mkdir()
    cache_root = tmp_path / "cache" / "stand-alone-analyzer" / "thumbnails" / sha
    (cache_root / "lod1").mkdir(parents=True)
    Image.fromarray(np.zeros((120, 192, 3), dtype=np.uint8)).save(
        cache_root / "lod1" / "ix000_iy000.webp"
    )
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(folder),
        "thumbnails_cache_dir": str(folder / "00_thumbnails"),
        "steps": {"thumbnails": {"completed_at": "x", "params": {}, "params_hash": "ph",
                                  "input_hashes": {}, "outputs": {}}},
        "image_id_to_stem": {0: "ix000_iy000"},
    }))
    (folder / "00_thumbnails").mkdir()
    (folder / "00_thumbnails" / "index.json").write_text(json.dumps({
        "version": 1,
        "lod_sizes": {"0": [64, 40], "1": [192, 120], "2": [480, 300]},
        "signature": ["sig0"],
        "cache_dir": str(cache_root),
    }))
    return folder


def _seed_in_folder_layout(tmp_path: Path) -> Path:
    """v0.2.15 legacy layout — tiles directly under 00_thumbnails/lodN/."""
    folder = tmp_path / "analysis"
    folder.mkdir()
    (folder / "00_thumbnails" / "lod1").mkdir(parents=True)
    Image.fromarray(np.zeros((120, 192, 3), dtype=np.uint8)).save(
        folder / "00_thumbnails" / "lod1" / "ix000_iy000.webp"
    )
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(folder),
        "thumbnails_cache_dir": str(folder / "00_thumbnails"),
        "steps": {"thumbnails": {"completed_at": "x", "params": {}, "params_hash": "ph",
                                  "input_hashes": {}, "outputs": {}}},
        "image_id_to_stem": {0: "ix000_iy000"},
    }))
    (folder / "00_thumbnails" / "index.json").write_text(json.dumps({
        "version": 1,
        "lod_sizes": {"0": [64, 40], "1": [192, 120], "2": [480, 300]},
        "signature": ["sig0"],
        # NOTE: no cache_dir key — triggers the FileResponse fallback path.
    }))
    return folder


@pytest.mark.asyncio
async def test_xaccel_redirect_emitted_when_cache_dir_present(tmp_path: Path, monkeypatch):
    folder = _seed_local_cache_layout(tmp_path, sha="deadbeef12345678")
    app = create_app()
    manifest = Manifest(analysis_folder=str(folder))

    async def _fake_get_manifest(project_id, scan_id):
        return manifest

    monkeypatch.setattr(
        "flake_analysis.api.routes.static.get_manifest", _fake_get_manifest
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/scans/1/static/thumbnails/lod1/ix000_iy000.webp"
        )
    assert resp.status_code == 200
    xa = resp.headers.get("x-accel-redirect", "")
    assert xa == "/_tiles_internal/deadbeef12345678/lod1/ix000_iy000.webp", (
        f"expected canonical X-Accel-Redirect path, got {xa!r}"
    )
    # Body MUST be empty so nginx serves the file (Option B).
    assert resp.content == b"", (
        f"X-Accel-Redirect responses must have empty bodies; got {len(resp.content)} bytes"
    )
    # Cache-Control + ETag preserved from the Plan 4 implementation.
    cc = resp.headers.get("cache-control", "")
    assert "max-age=86400" in cc
    assert resp.headers.get("etag", "") != ""


@pytest.mark.asyncio
async def test_xaccel_redirect_uses_internal_prefix_only(tmp_path: Path, monkeypatch):
    folder = _seed_local_cache_layout(tmp_path, sha="cafe1234abcd5678")
    app = create_app()
    manifest = Manifest(analysis_folder=str(folder))

    async def _fake_get_manifest(project_id, scan_id):
        return manifest

    monkeypatch.setattr(
        "flake_analysis.api.routes.static.get_manifest", _fake_get_manifest
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/scans/1/static/thumbnails/lod1/ix000_iy000.webp"
        )
    assert resp.status_code == 200
    xa = resp.headers.get("x-accel-redirect", "")
    assert xa.startswith("/_tiles_internal/"), (
        f"X-Accel-Redirect must start with /_tiles_internal/ (deployment-design §2.1); "
        f"got {xa!r}"
    )


@pytest.mark.asyncio
async def test_legacy_in_folder_layout_falls_back_to_file_response(tmp_path: Path, monkeypatch):
    folder = _seed_in_folder_layout(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(folder))

    async def _fake_get_manifest(project_id, scan_id):
        return manifest

    monkeypatch.setattr(
        "flake_analysis.api.routes.static.get_manifest", _fake_get_manifest
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/scans/1/static/thumbnails/lod1/ix000_iy000.webp"
        )
    assert resp.status_code == 200
    # No cache_dir → no X-Accel-Redirect; body holds the WebP bytes.
    assert "x-accel-redirect" not in {k.lower() for k in resp.headers.keys()}
    # WebP magic: "RIFF" .... "WEBP"
    assert resp.content[:4] == b"RIFF"
    assert resp.content[8:12] == b"WEBP"


@pytest.mark.asyncio
async def test_thumbnail_404_when_file_missing(tmp_path: Path, monkeypatch):
    folder = _seed_local_cache_layout(tmp_path, sha="deadbeef12345678")
    # Delete the WebP after seeding so the route's existence check fails.
    (Path(json.loads((folder / "00_thumbnails" / "index.json").read_text())["cache_dir"])
     / "lod1" / "ix000_iy000.webp").unlink()
    app = create_app()
    manifest = Manifest(analysis_folder=str(folder))

    async def _fake_get_manifest(project_id, scan_id):
        return manifest

    monkeypatch.setattr(
        "flake_analysis.api.routes.static.get_manifest", _fake_get_manifest
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/scans/1/static/thumbnails/lod1/ix000_iy000.webp"
        )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "thumbnail_missing"
