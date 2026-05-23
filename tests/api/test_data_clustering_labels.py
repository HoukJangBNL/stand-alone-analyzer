"""W10-C.4a: GET /projects/{pid}/scans/{sid}/data/clustering/labels."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
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


def _seed_folder(folder: Path) -> None:
    folder.mkdir(parents=True)
    save_manifest(Manifest(analysis_folder=str(folder)), folder)


@pytest.mark.asyncio
async def test_get_clustering_labels_404_when_missing(
    tmp_path, monkeypatch, pg_session, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    _seed_folder(analysis_folder(tmp_path, pid, sid))

    async with await _client(pg_session, user) as client:
        r = await client.get(
            f"/api/v1/projects/{pid}/scans/{sid}/data/clustering/labels"
        )
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "clustering_not_fitted"


@pytest.mark.asyncio
async def test_get_clustering_labels_returns_json(
    tmp_path, monkeypatch, pg_session, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    folder = analysis_folder(tmp_path, pid, sid)
    _seed_folder(folder)
    (folder / "04_clustering").mkdir(parents=True)
    payload = {
        "version": 1,
        "n_clusters": 1,
        "groups": [{"id": 0, "name": "a", "size": 3, "mean_rgb": [0.1, 0.2, 0.3]}],
        "assignments": {"1": 0, "2": 0, "3": 0},
        "thresholds": {"0": 0.5},
        "noise_label": -1,
        "random_state": 42,
        "fitted_at": "2026-05-21T00:00:00Z",
    }
    (folder / "04_clustering" / "labels.json").write_text(json.dumps(payload))

    async with await _client(pg_session, user) as client:
        r = await client.get(
            f"/api/v1/projects/{pid}/scans/{sid}/data/clustering/labels"
        )
    assert r.status_code == 200
    body = r.json()
    assert body["n_clusters"] == 1
    assert body["thresholds"]["0"] == 0.5
