"""W10-C.4a: GET /projects/{pid}/scans/{sid}/data/clustering/seed_groups."""
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
async def test_seed_groups_returns_empty_when_missing(
    tmp_path, monkeypatch, pg_session, sample_user_factory, sample_scan_factory
):
    """Missing file is the empty-list autoload contract, not a 404."""
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    _seed_folder(analysis_folder(tmp_path, pid, sid))

    async with await _client(pg_session, user) as client:
        r = await client.get(
            f"/api/v1/projects/{pid}/scans/{sid}/data/clustering/seed_groups"
        )
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_seed_groups_returns_list(
    tmp_path, monkeypatch, pg_session, sample_user_factory, sample_scan_factory
):
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid, sid = scan.project_id, scan.id
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    folder = analysis_folder(tmp_path, pid, sid)
    _seed_folder(folder)
    (folder / "04_clustering").mkdir(parents=True)
    payload = [
        {"name": "thin", "domain_ids": [1, 2, 3]},
        {"name": "thick", "domain_ids": [4, 5]},
    ]
    (folder / "04_clustering" / "seed_groups.json").write_text(json.dumps(payload))

    async with await _client(pg_session, user) as client:
        r = await client.get(
            f"/api/v1/projects/{pid}/scans/{sid}/data/clustering/seed_groups"
        )
    assert r.status_code == 200
    body = r.json()
    assert body == payload
