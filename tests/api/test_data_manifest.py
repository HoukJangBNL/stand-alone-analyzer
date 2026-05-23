"""W10-C.4a: GET /projects/{pid}/scans/{sid}/data/manifest."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.routes import data as data_route
from flake_analysis.state.manifest import Manifest, StepEntry, save_manifest
from flake_analysis.state.paths import analysis_folder

pytestmark = pytest.mark.pg


def _make_app() -> FastAPI:
    """Mini-app exposing only the data router (W10-C.4a)."""
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


@pytest.mark.asyncio
async def test_get_manifest(
    tmp_path,
    monkeypatch,
    pg_session,
    sample_user_factory,
    sample_scan_factory,
):
    """GET /projects/{pid}/scans/{sid}/data/manifest returns ManifestModel.

    With no Analysis row in DB, ``get_active_analysis`` returns None and the
    on-disk manifest passes through unchanged (per merge_db_steps_into_manifest).
    """
    user = await sample_user_factory()
    scan = await sample_scan_factory()
    pid = scan.project_id
    sid = scan.id

    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    folder = analysis_folder(tmp_path, pid, sid)
    folder.mkdir(parents=True)

    m = Manifest(analysis_folder=str(folder))
    m.steps["thumbnails"] = StepEntry(
        completed_at=datetime.now(timezone.utc).isoformat(),
        params={"quality": 80},
        params_hash="sha256:abc",
    )
    save_manifest(m, folder)

    async with await _client(pg_session, user) as client:
        resp = await client.get(f"/api/v1/projects/{pid}/scans/{sid}/data/manifest")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["version"] == 1
        assert "thumbnails" in body["steps"]
        assert body["steps"]["thumbnails"]["params"]["quality"] == 80
