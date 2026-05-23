"""W10-C.4a: GET /projects/{pid}/scans/{sid}/data/domain_stats."""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pyarrow.ipc as ipc
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


def _seed_stats(folder: Path) -> None:
    folder.mkdir(parents=True)
    raw = folder.parent / "raw"
    raw.mkdir(exist_ok=True)
    stats_dir = folder / "02_domain_stats"
    stats_dir.mkdir()
    np.savez(
        stats_dir / "stats.npz",
        flake_ids=np.array([1, 2, 3], dtype=np.int64),
        repr_rgbs=np.array([[10, 20, 30], [40, 50, 60], [70, 80, 90]], dtype=np.float64),
        std_pcts=np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]], dtype=np.float64),
        areas=np.array([100.0, 200.0, 300.0], dtype=np.float64),
        sam2=np.array([0.1, 0.5, 0.9], dtype=np.float64),
    )
    save_manifest(
        Manifest(analysis_folder=str(folder), raw_images_dir=str(raw)),
        folder,
    )


@pytest.mark.asyncio
async def test_data_domain_stats_json(
    tmp_path, monkeypatch, pg_session, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    _seed_stats(analysis_folder(tmp_path, pid, sid))

    async with await _client(pg_session, user) as client:
        r = await client.get(f"/api/v1/projects/{pid}/scans/{sid}/data/domain_stats")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")
        payload = r.json()
        assert payload["flake_ids"] == [1, 2, 3]
        assert payload["areas"] == [100.0, 200.0, 300.0]
        assert payload["sam2"] == [0.1, 0.5, 0.9]
        # std_pcts split into 3 columns for typed-array consumption
        assert payload["std_r"] == [1.0, 4.0, 7.0]
        assert payload["std_g"] == [2.0, 5.0, 8.0]
        assert payload["std_b"] == [3.0, 6.0, 9.0]
        # repr_rgbs likewise
        assert payload["mean_r"] == [10.0, 40.0, 70.0]


@pytest.mark.asyncio
async def test_data_domain_stats_arrow(
    tmp_path, monkeypatch, pg_session, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    _seed_stats(analysis_folder(tmp_path, pid, sid))

    async with await _client(pg_session, user) as client:
        r = await client.get(
            f"/api/v1/projects/{pid}/scans/{sid}/data/domain_stats",
            headers={"Accept": "application/vnd.apache.arrow.stream"},
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/vnd.apache.arrow.stream"
        reader = ipc.open_stream(io.BytesIO(r.content))
        table = reader.read_all()
        assert table.num_rows == 3
        assert table.column("flake_ids").to_pylist() == [1, 2, 3]


@pytest.mark.asyncio
async def test_data_domain_stats_missing_npz(
    tmp_path, monkeypatch, pg_session, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    folder = analysis_folder(tmp_path, pid, sid)
    folder.mkdir(parents=True)
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    save_manifest(
        Manifest(analysis_folder=str(folder), raw_images_dir=str(raw)),
        folder,
    )

    async with await _client(pg_session, user) as client:
        r = await client.get(f"/api/v1/projects/{pid}/scans/{sid}/data/domain_stats")
        assert r.status_code == 404
        body = r.json()
        assert body["error"]["code"] == "domain_stats_not_found"
