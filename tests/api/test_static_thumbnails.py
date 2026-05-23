"""W10-C.4a: GET /projects/{pid}/scans/{sid}/static/thumbnails/lod{lod}/{stem}.webp.

Happy path + path traversal + ETag + Cache-Control.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.routes import static as static_route
from flake_analysis.state.paths import analysis_folder

pytestmark = pytest.mark.pg


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(static_route.router, prefix="/api/v1")
    return app


async def _client(current_user):
    from flake_analysis.api.auth import get_current_user

    app = _make_app()
    app.dependency_overrides[get_current_user] = lambda: current_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _seed_thumbs(folder: Path) -> None:
    folder.mkdir(parents=True)
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
        "steps": {"thumbnails": {"completed_at": "x", "params": {}, "params_hash": "th",
                                  "input_hashes": {}, "outputs": {}}},
    }))


@pytest.mark.asyncio
async def test_thumbnail_happy_path_returns_webp_bytes(
    tmp_path, monkeypatch, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    _seed_thumbs(analysis_folder(tmp_path, pid, sid))

    async with await _client(user) as ac:
        resp = await ac.get(
            f"/api/v1/projects/{pid}/scans/{sid}/static/thumbnails/lod0/ix003_iy017.webp"
        )
    assert resp.status_code == 200
    assert resp.headers.get("content-type") in ("image/webp", "image/webp; charset=utf-8")
    # Bytes start with RIFF for WebP
    assert resp.content[:4] == b"RIFF"


@pytest.mark.asyncio
async def test_thumbnail_emits_etag_and_cache_control(
    tmp_path, monkeypatch, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    _seed_thumbs(analysis_folder(tmp_path, pid, sid))

    async with await _client(user) as ac:
        resp = await ac.get(
            f"/api/v1/projects/{pid}/scans/{sid}/static/thumbnails/lod0/ix003_iy017.webp"
        )
    assert resp.status_code == 200
    cc = resp.headers.get("cache-control", "")
    assert "max-age=86400" in cc
    assert "immutable" in cc
    etag = resp.headers.get("etag", "")
    assert etag.startswith("th:")  # params_hash:signature


@pytest.mark.asyncio
async def test_thumbnail_rejects_dot_dot_in_stem(
    tmp_path, monkeypatch, sample_user_factory, sample_scan_factory
):
    """The headline path-traversal negative test."""
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    _seed_thumbs(analysis_folder(tmp_path, pid, sid))

    async with await _client(user) as ac:
        resp = await ac.get(
            f"/api/v1/projects/{pid}/scans/{sid}/static/thumbnails/lod0/..%2F..%2F..%2Fetc%2Fpasswd"
        )
    assert resp.status_code in (400, 404)
    assert resp.status_code != 200


@pytest.mark.asyncio
async def test_thumbnail_rejects_absolute_path_in_stem(
    tmp_path, monkeypatch, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    _seed_thumbs(analysis_folder(tmp_path, pid, sid))

    async with await _client(user) as ac:
        resp = await ac.get(
            f"/api/v1/projects/{pid}/scans/{sid}/static/thumbnails/lod0/%2Fetc%2Fpasswd"
        )
    assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_thumbnail_404_when_lod_dir_missing(
    tmp_path, monkeypatch, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    _seed_thumbs(analysis_folder(tmp_path, pid, sid))

    async with await _client(user) as ac:
        resp = await ac.get(
            f"/api/v1/projects/{pid}/scans/{sid}/static/thumbnails/lod9/ix003_iy017.webp"
        )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "thumbnail_missing"


@pytest.mark.asyncio
async def test_thumbnail_404_when_stem_missing(
    tmp_path, monkeypatch, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    _seed_thumbs(analysis_folder(tmp_path, pid, sid))

    async with await _client(user) as ac:
        resp = await ac.get(
            f"/api/v1/projects/{pid}/scans/{sid}/static/thumbnails/lod0/missing_stem.webp"
        )
    assert resp.status_code == 404
