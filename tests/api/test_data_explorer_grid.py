import json
from pathlib import Path
from unittest.mock import AsyncMock

import numpy as np
import pytest
from PIL import Image
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.logging_ctx import RequestIdMiddleware
from flake_analysis.api.routes import explorer as explorer_route
from flake_analysis.state.manifest import Manifest

SID = 42


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(explorer_route.router, prefix="/api/v1")
    return app


@pytest.fixture(autouse=True)
def _clear_scan_locks():
    from flake_analysis.api import mutex
    mutex._scan_locks.clear()
    yield
    mutex._scan_locks.clear()


def _seed_thumb_world(folder: Path) -> None:
    """Write the on-disk artifacts (raw images, thumb index, manifest.json with image_id_to_stem).

    The route's `get_manifest` is monkeypatched (load_manifest's strict dataclass
    parsing rejects `image_id_to_stem`); the *service* that builds the tile
    manifest reads manifest.json directly via `_read_manifest_json`, which IS
    happy with the extra key.
    """
    folder.mkdir(parents=True, exist_ok=True)
    raw = folder / "raw"; raw.mkdir(parents=True, exist_ok=True)
    cache = folder / "00_thumbnails"; cache.mkdir(parents=True, exist_ok=True)
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


def _patch_get_manifest(monkeypatch, folder: Path) -> None:
    """Override the route-local get_manifest to skip strict dataclass loading."""
    manifest = Manifest(analysis_folder=str(folder))
    monkeypatch.setattr(
        "flake_analysis.api.routes.explorer.get_manifest",
        AsyncMock(return_value=manifest),
    )


@pytest.mark.asyncio
async def test_grid_returns_payload_with_tiles_and_signature(tmp_path: Path, monkeypatch):
    _seed_thumb_world(tmp_path)
    _patch_get_manifest(monkeypatch, tmp_path)
    app = _make_app()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/projects/local/scans/{SID}/explorer/grid")
    assert resp.status_code == 200
    payload = resp.json()
    assert "grid_w" in payload
    assert "grid_h" in payload
    assert "lod_sizes" in payload
    assert "signature" in payload
    assert "tiles" in payload
    assert isinstance(payload["tiles"], list)


@pytest.mark.asyncio
async def test_grid_etag_matches_tile_manifest(tmp_path: Path, monkeypatch):
    """Pinned decision #11: /explorer/grid is the canonical URL; same identity contract as tile_manifest."""
    _seed_thumb_world(tmp_path)
    _patch_get_manifest(monkeypatch, tmp_path)
    app = _make_app()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        a = await ac.get(f"/api/v1/projects/local/scans/{SID}/explorer/tile_manifest")
        b = await ac.get(f"/api/v1/projects/local/scans/{SID}/explorer/grid")
    assert a.headers.get("etag") == b.headers.get("etag")
