import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.logging_ctx import RequestIdMiddleware
from flake_analysis.api.routes import clustering as clustering_route
from flake_analysis.state.manifest import Manifest, save_manifest
from flake_analysis.state.paths import analysis_folder

SID = 42


def _make_app() -> FastAPI:
    """Mini-app exposing only the clustering router (W10-C.4c)."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(clustering_route.router, prefix="/api/v1")
    return app


@pytest.fixture(autouse=True)
def _clear_scan_locks():
    from flake_analysis.api import mutex
    mutex._scan_locks.clear()
    yield
    mutex._scan_locks.clear()


def _setup(tmp_path: Path, monkeypatch, pid: str = "local", sid: int = SID) -> Path:
    folder = analysis_folder(tmp_path, pid, sid)
    folder.mkdir(parents=True)
    save_manifest(Manifest(analysis_folder=str(folder)), folder)
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    return folder


def _write_minimal_clustering(folder: Path) -> None:
    (folder / "04_clustering").mkdir(parents=True)
    df = pd.DataFrame({
        "domain_id": [1, 2, 3],
        "cluster_label": [0, 1, 0],
        "max_posterior": [0.9, 0.8, 0.4],
    })
    df.to_parquet(folder / "04_clustering" / "assignments.parquet", index=False)
    labels = {
        "version": 1,
        "n_clusters": 2,
        "groups": [
            {"id": 0, "name": "a", "size": 2, "mean_rgb": [0, 0, 0]},
            {"id": 1, "name": "b", "size": 1, "mean_rgb": [0, 0, 0]},
        ],
        "assignments": {"1": 0, "2": 1, "3": 0},
        "thresholds": {"0": 0.5, "1": 0.5},
        "noise_label": -1,
        "random_state": 42,
        "fitted_at": "2026-05-21T00:00:00Z",
    }
    (folder / "04_clustering" / "labels.json").write_text(json.dumps(labels))


@pytest.mark.asyncio
async def test_apply_thresholds_streams_error_without_clustering(tmp_path: Path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    app = _make_app()

    body = {"cluster_thresholds": {0: 0.7}}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream(
            "POST",
            f"/api/v1/projects/local/scans/{SID}/run/clustering/apply_thresholds",
            json=body,
        ) as r:
            assert r.status_code == 200
            text = ""
            async for chunk in r.aiter_text():
                text += chunk
            assert "error" in text


@pytest.mark.asyncio
async def test_apply_thresholds_streams_done_with_summary(tmp_path: Path, monkeypatch):
    folder = _setup(tmp_path, monkeypatch)
    _write_minimal_clustering(folder)

    app = _make_app()

    body = {"cluster_thresholds": {0: 0.5, 1: 0.5}}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream(
            "POST",
            f"/api/v1/projects/local/scans/{SID}/run/clustering/apply_thresholds",
            json=body,
        ) as r:
            assert r.status_code == 200
            text = ""
            async for chunk in r.aiter_text():
                text += chunk
            assert "done" in text
            assert "n_pass" in text
