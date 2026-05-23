from pathlib import Path

import pandas as pd
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.logging_ctx import RequestIdMiddleware
from flake_analysis.api.routes import selector as selector_route
from flake_analysis.state.manifest import Manifest, save_manifest
from flake_analysis.state.paths import analysis_folder

PID = "local"
SID = 42


def _make_app() -> FastAPI:
    """Mini-app exposing only the selector router (W10-C.4b)."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(selector_route.router, prefix="/api/v1")
    return app


def _setup(tmp_path: Path, monkeypatch) -> Path:
    folder = analysis_folder(tmp_path, PID, SID)
    folder.mkdir(parents=True)
    (folder / "03_selector").mkdir()
    pd.DataFrame({
        "domain_id": [1, 2, 3, 4],
        "selected": [True, False, True, True],
    }).to_parquet(folder / "03_selector" / "selection.parquet", index=False)
    save_manifest(
        Manifest(analysis_folder=str(folder), raw_images_dir=str(tmp_path / "raw")),
        folder,
    )
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    return folder


@pytest.mark.asyncio
async def test_export_selected_only(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get(
            f"/api/v1/projects/{PID}/scans/{SID}/selector/export",
            params={"mode": "selected"},
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        lines = r.text.strip().splitlines()
        assert lines[0] == "domain_id,selected"
        assert {l for l in lines[1:]} == {"1,True", "3,True", "4,True"}


@pytest.mark.asyncio
async def test_export_filtered_returns_all_rows(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get(
            f"/api/v1/projects/{PID}/scans/{SID}/selector/export",
            params={"mode": "filtered"},
        )
        assert r.status_code == 200
        lines = r.text.strip().splitlines()
        assert len(lines) == 5  # header + 4 rows


@pytest.mark.asyncio
async def test_export_invalid_mode(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get(
            f"/api/v1/projects/{PID}/scans/{SID}/selector/export",
            params={"mode": "garbage"},
        )
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_export_missing_file_returns_404(tmp_path, monkeypatch):
    """Verify SelectionNotFound (404) when selection.parquet does not exist."""
    folder = analysis_folder(tmp_path, PID, SID)
    folder.mkdir(parents=True)
    raw = tmp_path / "raw"
    raw.mkdir()
    save_manifest(
        Manifest(analysis_folder=str(folder), raw_images_dir=str(raw)),
        folder,
    )
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get(
            f"/api/v1/projects/{PID}/scans/{SID}/selector/export",
            params={"mode": "selected"},
        )
        assert r.status_code == 404
        body = r.json()
        assert body["error"]["code"] == "selection_not_found"
