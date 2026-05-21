import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


def _seed_thumb_world(folder: Path) -> None:
    raw = folder / "raw"; raw.mkdir(parents=True)
    cache = folder / "00_thumbnails"; cache.mkdir(parents=True)
    image_id_to_stem = {0: "ix000_iy000", 1: "ix001_iy000"}
    for stem in image_id_to_stem.values():
        Image.fromarray(np.zeros((60, 80, 3), dtype=np.uint8)).save(raw / f"{stem}.png")
    (cache / "index.json").write_text(json.dumps({
        "version": 1,
        "lod_sizes": {"0": [64, 48]},
        "signature": ["sig0", "sig1"],
    }))
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(raw),
        "steps": {"thumbnails": {"completed_at": "x", "params": {}, "params_hash": "h",
                                  "input_hashes": {}, "outputs": {}}},
        "image_id_to_stem": image_id_to_stem,
    }))


@pytest.mark.asyncio
async def test_grid_returns_payload_with_tiles_and_signature(tmp_path: Path):
    _seed_thumb_world(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/explorer/grid")
    assert resp.status_code == 200
    payload = resp.json()
    assert "grid_w" in payload
    assert "grid_h" in payload
    assert "lod_sizes" in payload
    assert "signature" in payload
    assert "tiles" in payload
    assert isinstance(payload["tiles"], list)


@pytest.mark.asyncio
async def test_grid_etag_matches_tile_manifest(tmp_path: Path):
    """Pinned decision #11: /explorer/grid is the canonical URL; same identity contract as tile_manifest."""
    _seed_thumb_world(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        a = await ac.get("/api/v1/projects/local/explorer/tile_manifest")
        b = await ac.get("/api/v1/projects/local/explorer/grid")
    assert a.headers.get("etag") == b.headers.get("etag")
