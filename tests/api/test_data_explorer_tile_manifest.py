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
    folder.mkdir(parents=True, exist_ok=True)
    raw = folder / "raw"; raw.mkdir(parents=True, exist_ok=True)
    cache = folder / "00_thumbnails"; cache.mkdir(parents=True, exist_ok=True)
    image_id_to_stem = {0: "ix000_iy000", 1: "ix001_iy000"}
    for stem in image_id_to_stem.values():
        Image.fromarray(np.zeros((60, 80, 3), dtype=np.uint8)).save(raw / f"{stem}.png")
        for lod, (w, h) in [(0, (64, 48)), (1, (192, 144))]:
            (cache / f"lod{lod}").mkdir(parents=True, exist_ok=True)
            Image.fromarray(np.zeros((h, w, 3), dtype=np.uint8)).save(
                cache / f"lod{lod}" / f"{stem}.webp"
            )
    (cache / "index.json").write_text(json.dumps({
        "version": 1,
        "lod_sizes": {"0": [64, 48], "1": [192, 144]},
        "signature": ["sig0", "sig1"],
    }))
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1,
        "analysis_folder": str(folder),
        "raw_images_dir": str(raw),
        "thumbnails_cache_dir": str(cache),
        "annotations_path": str(folder / "annotations.json"),
        "steps": {
            "thumbnails": {
                "completed_at": "2026-05-21T00:00:00Z",
                "params": {}, "params_hash": "thumb_hash",
                "input_hashes": {}, "outputs": {"index_json": "00_thumbnails/index.json"},
            },
        },
        "image_id_to_stem": image_id_to_stem,
    }))


def _patch_get_manifest(monkeypatch, folder: Path) -> None:
    """Override the route-local get_manifest (load_manifest is strict on extra keys)."""
    manifest = Manifest(analysis_folder=str(folder))
    monkeypatch.setattr(
        "flake_analysis.api.routes.explorer.get_manifest",
        AsyncMock(return_value=manifest),
    )


@pytest.mark.asyncio
async def test_tile_manifest_returns_grid_and_tiles(tmp_path: Path, monkeypatch):
    _seed_thumb_world(tmp_path)
    _patch_get_manifest(monkeypatch, tmp_path)
    app = _make_app()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            f"/api/v1/projects/local/scans/{SID}/explorer/tile_manifest"
        )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["grid_w"] == 2
    assert payload["grid_h"] == 1
    assert payload["params_hash"] == "thumb_hash"
    assert len(payload["tiles"]) == 2
    assert payload["lod_sizes"]["1"] == [192, 144]


@pytest.mark.asyncio
async def test_tile_manifest_emits_etag_and_cache_control(tmp_path: Path, monkeypatch):
    _seed_thumb_world(tmp_path)
    _patch_get_manifest(monkeypatch, tmp_path)
    app = _make_app()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            f"/api/v1/projects/local/scans/{SID}/explorer/tile_manifest"
        )
    assert resp.status_code == 200
    assert resp.headers.get("etag", "").startswith("thumb_hash:")
    assert "no-store" not in resp.headers.get("cache-control", "")


@pytest.mark.asyncio
async def test_tile_manifest_404_when_thumbnails_missing(tmp_path: Path, monkeypatch):
    """No 00_thumbnails/index.json → ArtifactMissing 404."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(tmp_path),
        "raw_images_dir": str(tmp_path / "raw"),
        "steps": {}, "image_id_to_stem": {},
    }))
    _patch_get_manifest(monkeypatch, tmp_path)
    app = _make_app()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            f"/api/v1/projects/local/scans/{SID}/explorer/tile_manifest"
        )
    assert resp.status_code == 404
