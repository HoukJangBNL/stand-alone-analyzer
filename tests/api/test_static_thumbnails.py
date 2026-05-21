"""Static thumbnail route tests — happy path + path traversal + ETag + Cache-Control."""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


def _seed_thumbs(folder: Path) -> None:
    cache = folder / "00_thumbnails"
    for lod, (w, h) in [(0, (64, 48)), (1, (192, 144))]:
        d = cache / f"lod{lod}"
        d.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.zeros((h, w, 3), dtype=np.uint8)).save(d / "ix003_iy017.webp")
    (cache / "index.json").write_text(json.dumps({
        "version": 1,
        "lod_sizes": {"0": [64, 48], "1": [192, 144]},
        "signature": ["sig0", "sig1"],
    }))
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(folder / "raw"),
        "thumbnails_cache_dir": str(cache),
        "steps": {"thumbnails": {"completed_at": "x", "params": {}, "params_hash": "th",
                                  "input_hashes": {}, "outputs": {}}},
    }))


@pytest.mark.asyncio
async def test_thumbnail_happy_path_returns_webp_bytes(tmp_path: Path):
    _seed_thumbs(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/static/thumbnails/lod0/ix003_iy017.webp")
    assert resp.status_code == 200
    assert resp.headers.get("content-type") in ("image/webp", "image/webp; charset=utf-8")
    # Bytes start with RIFF for WebP
    assert resp.content[:4] == b"RIFF"


@pytest.mark.asyncio
async def test_thumbnail_emits_etag_and_cache_control(tmp_path: Path):
    _seed_thumbs(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/static/thumbnails/lod0/ix003_iy017.webp")
    assert resp.status_code == 200
    cc = resp.headers.get("cache-control", "")
    assert "max-age=86400" in cc
    assert "immutable" in cc
    etag = resp.headers.get("etag", "")
    assert etag.startswith("th:")  # params_hash:signature


@pytest.mark.asyncio
async def test_thumbnail_rejects_dot_dot_in_stem(tmp_path: Path):
    """The headline path-traversal negative test."""
    _seed_thumbs(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/static/thumbnails/lod0/..%2F..%2F..%2Fetc%2Fpasswd"
        )
    assert resp.status_code in (400, 404)
    assert resp.status_code != 200


@pytest.mark.asyncio
async def test_thumbnail_rejects_absolute_path_in_stem(tmp_path: Path):
    _seed_thumbs(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/static/thumbnails/lod0/%2Fetc%2Fpasswd"
        )
    assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_thumbnail_404_when_lod_dir_missing(tmp_path: Path):
    _seed_thumbs(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/static/thumbnails/lod9/ix003_iy017.webp"
        )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "thumbnail_missing"


@pytest.mark.asyncio
async def test_thumbnail_404_when_stem_missing(tmp_path: Path):
    _seed_thumbs(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/v1/projects/local/static/thumbnails/lod0/missing_stem.webp"
        )
    assert resp.status_code == 404
