"""Static raw image route tests — happy path + path traversal + ETag + Cache-Control."""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


def _seed_raw(folder: Path) -> None:
    raw = folder / "raw"; raw.mkdir(parents=True)
    Image.fromarray(np.zeros((60, 80, 3), dtype=np.uint8)).save(raw / "ix003_iy017.png")
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(raw),
        "thumbnails_cache_dir": str(folder / "00_thumbnails"),
        "steps": {"thumbnails": {"completed_at": "x", "params": {}, "params_hash": "rh",
                                  "input_hashes": {}, "outputs": {}}},
    }))
    (folder / "00_thumbnails").mkdir(exist_ok=True)
    (folder / "00_thumbnails" / "index.json").write_text(json.dumps({
        "version": 1, "lod_sizes": {}, "signature": ["raw_sig"],
    }))


@pytest.mark.asyncio
async def test_raw_happy_path_returns_png_bytes(tmp_path: Path):
    _seed_raw(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/static/raw/ix003_iy017.png")
    assert resp.status_code == 200
    assert resp.headers.get("content-type") in ("image/png", "image/png; charset=utf-8")
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_raw_emits_etag_and_cache_control(tmp_path: Path):
    _seed_raw(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/static/raw/ix003_iy017.png")
    assert resp.status_code == 200
    cc = resp.headers.get("cache-control", "")
    assert "max-age=86400" in cc
    assert "immutable" in cc
    etag = resp.headers.get("etag", "")
    assert "rh" in etag


@pytest.mark.asyncio
async def test_raw_rejects_dot_dot(tmp_path: Path):
    _seed_raw(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/static/raw/..%2F..%2Fetc%2Fpasswd"
        )
    assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_raw_rejects_absolute(tmp_path: Path):
    _seed_raw(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/static/raw/%2Fetc%2Fpasswd"
        )
    assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_raw_404_when_filename_missing(tmp_path: Path):
    _seed_raw(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/static/raw/nonexistent.png")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "raw_image_missing"
