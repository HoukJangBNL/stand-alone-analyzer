"""W10-C: GET /projects/{pid}/scans listing."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.routes import scans as scans_route

pytestmark = pytest.mark.pg


def _make_app() -> FastAPI:
    """Build a minimal FastAPI app exposing only the scans router.

    Mirrors `tests/api/test_projects.py::_make_app` — sidesteps W10-C Task 4's
    not-yet-migrated analysis routers (data/run/selector/clustering/explorer/
    static) which still use the pre-W10-B `Depends(get_active_analysis)` shape
    and crash at route-collection time. Once Task 4 lands, both files can
    switch to importing the full `app` from `flake_analysis.api.main`.
    """
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(scans_route.router, prefix="/api/v1")
    return app


async def _client(pg_session, current_user):
    """Wire the test app's get_db_session + get_current_user to test fixtures."""
    from flake_analysis.api.auth import get_current_user
    from flake_analysis.api.deps import get_db_session

    app = _make_app()

    async def _override_db():
        yield pg_session

    app.dependency_overrides[get_db_session] = _override_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_list_scans_for_project_empty(
    pg_session, sample_user_factory, sample_project_factory,
):
    user = await sample_user_factory()
    proj = await sample_project_factory(owner=user)
    await pg_session.commit()

    async with await _client(pg_session, user) as client:
        r = await client.get(f"/api/v1/projects/{proj.id}/scans")
        assert r.status_code == 200, r.text
        assert r.json() == {"scans": []}


@pytest.mark.asyncio
async def test_list_scans_returns_only_for_that_project(
    pg_session, sample_user_factory, sample_project_factory, sample_scan_factory,
):
    user = await sample_user_factory()
    p1 = await sample_project_factory(owner=user)
    p2 = await sample_project_factory(owner=user)
    await sample_scan_factory(project=p1, name="s1")
    await sample_scan_factory(project=p1, name="s2")
    await sample_scan_factory(project=p2, name="s3")
    await pg_session.commit()

    async with await _client(pg_session, user) as client:
        r = await client.get(f"/api/v1/projects/{p1.id}/scans")
        assert r.status_code == 200, r.text
        names = [s["name"] for s in r.json()["scans"]]
        assert sorted(names) == ["s1", "s2"]
