from pathlib import Path
from unittest.mock import patch

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


@pytest.fixture(autouse=True)
def _clear_scan_locks():
    from flake_analysis.api import mutex
    mutex._scan_locks.clear()
    yield
    mutex._scan_locks.clear()


def _setup_project(tmp_path, monkeypatch) -> Path:
    folder = analysis_folder(tmp_path, PID, SID)
    folder.mkdir(parents=True)
    raw = tmp_path / "raw"
    raw.mkdir()
    save_manifest(
        Manifest(analysis_folder=str(folder), raw_images_dir=str(raw)),
        folder,
    )
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    return folder


def _make_pipeline_mock(folder: Path):
    """Returns a stub run_selector_step that writes a 4-row selection.parquet."""
    def stub(**kwargs):
        out = folder / "03_selector"
        out.mkdir(parents=True, exist_ok=True)
        p = out / "selection.parquet"
        pd.DataFrame({
            "domain_id": [1, 2, 3, 4],
            "selected": [True, True, False, True],
        }).to_parquet(p, index=False)
        return {
            "output_path": str(p),
            "selected_count": 3,
            "total_count": 4,
            "params": {"area_min": 5.0},
            "params_hash": "sha256:abc",
        }
    return stub


@pytest.mark.asyncio
async def test_commit_no_lasso_returns_filter_count(tmp_path, monkeypatch):
    folder = _setup_project(tmp_path, monkeypatch)
    with patch(
        "flake_analysis.api.routes.selector.run_selector_step",
        side_effect=_make_pipeline_mock(folder),
    ):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                f"/api/v1/projects/{PID}/scans/{SID}/selector/commit",
                json={"params": {"area_min": 5.0}, "lasso_ids": None},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["n_committed"] == 3
            assert body["n_filter_accepted"] == 3
            assert body["n_lasso"] == 0
            assert body["total_count"] == 4


@pytest.mark.asyncio
async def test_commit_with_lasso_intersects(tmp_path, monkeypatch):
    folder = _setup_project(tmp_path, monkeypatch)
    with patch(
        "flake_analysis.api.routes.selector.run_selector_step",
        side_effect=_make_pipeline_mock(folder),
    ):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                f"/api/v1/projects/{PID}/scans/{SID}/selector/commit",
                json={"params": {"area_min": 5.0}, "lasso_ids": [2, 3]},
            )
            assert r.status_code == 200
            body = r.json()
            # filter: {1,2,4} accepted; lasso: {2,3}; intersection: {2}
            assert body["n_committed"] == 1
            assert body["n_filter_accepted"] == 3
            assert body["n_lasso"] == 2
            # Verify file actually rewritten
            df = pd.read_parquet(folder / "03_selector" / "selection.parquet")
            rows = dict(zip(df["domain_id"].tolist(), df["selected"].tolist()))
            assert rows == {1: False, 2: True, 3: False, 4: False}


@pytest.mark.asyncio
async def test_commit_without_domain_stats_returns_409(tmp_path, monkeypatch):
    """RuntimeError('Domain Stats step not completed') -> 409 prerequisite_missing."""
    _setup_project(tmp_path, monkeypatch)

    def boom(**_kw):
        raise RuntimeError("Domain Stats step not completed. Run Compute → Domain Stats first.")

    with patch(
        "flake_analysis.api.routes.selector.run_selector_step",
        side_effect=boom,
    ):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(
                f"/api/v1/projects/{PID}/scans/{SID}/selector/commit",
                json={"params": {"area_min": 5.0}, "lasso_ids": None},
            )
            assert r.status_code == 409
            body = r.json()
            assert body["error"]["code"] == "prerequisite_missing"
            assert "Domain Stats" in body["error"]["details"]["reason"]
