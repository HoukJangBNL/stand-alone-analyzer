"""W10-C.4a: GET /projects/{pid}/scans/{sid}/static/raw/{filename}.

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


def _seed_raw(folder: Path) -> None:
    folder.mkdir(parents=True)
    raw = folder / "raw"
    raw.mkdir(parents=True)
    Image.fromarray(np.zeros((60, 80, 3), dtype=np.uint8)).save(raw / "ix003_iy017.png")
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(raw),
        "steps": {"thumbnails": {"completed_at": "x", "params": {}, "params_hash": "rh",
                                  "input_hashes": {}, "outputs": {}}},
    }))
    (folder / "00_thumbnails").mkdir(exist_ok=True)
    (folder / "00_thumbnails" / "index.json").write_text(json.dumps({
        "version": 1, "lod_sizes": {}, "signature": ["raw_sig"],
    }))


@pytest.mark.asyncio
async def test_raw_happy_path_returns_png_bytes(
    tmp_path, monkeypatch, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    _seed_raw(analysis_folder(tmp_path, pid, sid))

    async with await _client(user) as ac:
        resp = await ac.get(
            f"/api/v1/projects/{pid}/scans/{sid}/static/raw/ix003_iy017.png"
        )
    assert resp.status_code == 200
    assert resp.headers.get("content-type") in ("image/png", "image/png; charset=utf-8")
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_raw_emits_etag_and_cache_control(
    tmp_path, monkeypatch, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    _seed_raw(analysis_folder(tmp_path, pid, sid))

    async with await _client(user) as ac:
        resp = await ac.get(
            f"/api/v1/projects/{pid}/scans/{sid}/static/raw/ix003_iy017.png"
        )
    assert resp.status_code == 200
    cc = resp.headers.get("cache-control", "")
    assert "max-age=86400" in cc
    assert "immutable" in cc
    etag = resp.headers.get("etag", "")
    assert "rh" in etag


@pytest.mark.asyncio
async def test_raw_rejects_dot_dot(
    tmp_path, monkeypatch, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    _seed_raw(analysis_folder(tmp_path, pid, sid))

    async with await _client(user) as ac:
        resp = await ac.get(
            f"/api/v1/projects/{pid}/scans/{sid}/static/raw/..%2F..%2Fetc%2Fpasswd"
        )
    assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_raw_rejects_absolute(
    tmp_path, monkeypatch, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    _seed_raw(analysis_folder(tmp_path, pid, sid))

    async with await _client(user) as ac:
        resp = await ac.get(
            f"/api/v1/projects/{pid}/scans/{sid}/static/raw/%2Fetc%2Fpasswd"
        )
    assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_raw_404_when_filename_missing(
    tmp_path, monkeypatch, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    _seed_raw(analysis_folder(tmp_path, pid, sid))

    async with await _client(user) as ac:
        resp = await ac.get(
            f"/api/v1/projects/{pid}/scans/{sid}/static/raw/nonexistent.png"
        )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "raw_image_missing"
