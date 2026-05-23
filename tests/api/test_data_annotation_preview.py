"""W10-C.4a: GET /projects/{pid}/scans/{sid}/data/annotations/{domain_id}/preview."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.routes import data as data_route
from flake_analysis.state.manifest import Manifest, save_manifest
from flake_analysis.state.paths import analysis_folder

pytestmark = pytest.mark.pg


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(data_route.router, prefix="/api/v1")
    return app


async def _client(pg_session, current_user):
    from flake_analysis.api.auth import get_current_user
    from flake_analysis.api.deps import get_db_session

    app = _make_app()

    async def _override_db():
        yield pg_session

    app.dependency_overrides[get_db_session] = _override_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _seed(tmp_path: Path, folder: Path) -> None:
    folder.mkdir(parents=True)
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    Image.new("RGB", (200, 200), (10, 20, 30)).save(raw / "tile_0.png")

    ann_path = tmp_path / "annotations.json"
    ann_path.write_text(json.dumps({
        "tile_0.png": {
            "domains": [
                {"domain_id": 7, "bbox": [50, 50, 100, 100], "polygon": [[50, 50], [100, 50], [100, 100], [50, 100]]},
            ],
        }
    }))

    save_manifest(
        Manifest(
            analysis_folder=str(folder),
            raw_images_dir=str(raw),
            annotations_path=str(ann_path),
        ),
        folder,
    )


@pytest.mark.asyncio
async def test_annotation_preview_returns_png(
    tmp_path, monkeypatch, pg_session, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    _seed(tmp_path, analysis_folder(tmp_path, pid, sid))

    async with await _client(pg_session, user) as client:
        r = await client.get(
            f"/api/v1/projects/{pid}/scans/{sid}/data/annotations/7/preview"
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        img = Image.open(io.BytesIO(r.content))
        assert img.size == (50, 50)


@pytest.mark.asyncio
async def test_annotation_preview_with_contour_query(
    tmp_path, monkeypatch, pg_session, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    _seed(tmp_path, analysis_folder(tmp_path, pid, sid))

    async with await _client(pg_session, user) as client:
        r = await client.get(
            f"/api/v1/projects/{pid}/scans/{sid}/data/annotations/7/preview",
            params={"with_contour": "true"},
        )
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_annotation_preview_404(
    tmp_path, monkeypatch, pg_session, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    _seed(tmp_path, analysis_folder(tmp_path, pid, sid))

    async with await _client(pg_session, user) as client:
        r = await client.get(
            f"/api/v1/projects/{pid}/scans/{sid}/data/annotations/9999/preview"
        )
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "domain_not_found"
