import json
from pathlib import Path
from unittest.mock import AsyncMock

import pandas as pd
import pytest
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


def _seed_clustering_world(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "04_clustering").mkdir(parents=True, exist_ok=True)
    (folder / "05_domain_proximity").mkdir(parents=True, exist_ok=True)
    (folder / "04_clustering" / "labels.json").write_text(json.dumps({
        "version": 1, "n_clusters": 2,
        "groups": [
            {"id": 0, "name": "thin", "size": 3, "mean_rgb": [0, 0, 0]},
            {"id": 1, "name": "thick", "size": 2, "mean_rgb": [0, 0, 0]},
        ],
        "assignments": {"10": 0, "11": 0, "12": 1, "20": 1, "21": 0},
        "thresholds": {"0": 0.5, "1": 0.5},
        "noise_label": -1, "random_state": 42, "fitted_at": "x",
    }))
    pd.DataFrame({
        "domain_id": [10, 11, 12, 20, 21],
        "cluster_id": [0, 0, 1, 1, 0],
        "posterior_p": [0.9, 0.8, 0.85, 0.7, 0.95],
    }).to_parquet(folder / "04_clustering" / "assignments.parquet", index=False)
    pd.DataFrame({
        "domain_id": [10, 11, 12, 20, 21],
        "flake_id":  [100, 100, 100, 200, 200],
        "flake_size": [3, 3, 3, 2, 2],
        "image_id":  [0, 0, 0, 1, 1],
    }).to_parquet(folder / "05_domain_proximity" / "flake_assignments.parquet", index=False)
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(folder / "raw"),
        "steps": {}, "image_id_to_stem": {},
    }))


def _patch_get_manifest(monkeypatch, folder: Path) -> None:
    """Override the route-local get_manifest (load_manifest is strict on extra keys)."""
    manifest = Manifest(analysis_folder=str(folder))
    monkeypatch.setattr(
        "flake_analysis.api.routes.explorer.get_manifest",
        AsyncMock(return_value=manifest),
    )


@pytest.mark.asyncio
async def test_flakes_no_filter_returns_all(tmp_path: Path, monkeypatch):
    _seed_clustering_world(tmp_path)
    _patch_get_manifest(monkeypatch, tmp_path)
    app = _make_app()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/projects/local/scans/{SID}/explorer/flakes")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 2
    flake_ids = sorted(r["flake_id"] for r in payload["rows"])
    assert flake_ids == [100, 200]


@pytest.mark.asyncio
async def test_flakes_include_query_filters(tmp_path: Path, monkeypatch):
    _seed_clustering_world(tmp_path)
    _patch_get_manifest(monkeypatch, tmp_path)
    app = _make_app()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            f"/api/v1/projects/local/scans/{SID}/explorer/flakes?include=thin"
        )
    assert resp.status_code == 200
    flake_ids = sorted(r["flake_id"] for r in resp.json()["rows"])
    assert flake_ids == [100, 200]


@pytest.mark.asyncio
async def test_flakes_exclude_query_filters(tmp_path: Path, monkeypatch):
    _seed_clustering_world(tmp_path)
    _patch_get_manifest(monkeypatch, tmp_path)
    app = _make_app()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            f"/api/v1/projects/local/scans/{SID}/explorer/flakes?exclude=thick"
        )
    assert resp.status_code == 200
    assert resp.json()["rows"] == []  # both flakes contain "thick"


@pytest.mark.asyncio
async def test_flakes_size_min_max(tmp_path: Path, monkeypatch):
    _seed_clustering_world(tmp_path)
    _patch_get_manifest(monkeypatch, tmp_path)
    app = _make_app()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            f"/api/v1/projects/local/scans/{SID}/explorer/flakes?size_min=3&size_max=3"
        )
    assert resp.status_code == 200
    flake_ids = [r["flake_id"] for r in resp.json()["rows"]]
    assert flake_ids == [100]


@pytest.mark.asyncio
async def test_flakes_pass_column_reflects_filter_invariant(tmp_path: Path, monkeypatch):
    """Route reads `pass` from the DataFrame; build_flake_table only returns passing rows."""
    _seed_clustering_world(tmp_path)
    _patch_get_manifest(monkeypatch, tmp_path)
    app = _make_app()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/api/v1/projects/local/scans/{SID}/explorer/flakes")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["rows"], "expected at least one row"
    assert payload["rows"][0]["pass"] is True


@pytest.mark.asyncio
async def test_flakes_404_when_clustering_missing(tmp_path: Path, monkeypatch):
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
        resp = await ac.get(f"/api/v1/projects/local/scans/{SID}/explorer/flakes")
    assert resp.status_code == 404
