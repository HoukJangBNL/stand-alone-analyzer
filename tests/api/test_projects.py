"""W10-C: projects CRUD HTTP integration tests."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.routes import projects as projects_route
from flake_analysis.db.models import Scan

pytestmark = pytest.mark.pg


def _make_app() -> FastAPI:
    """Build a minimal FastAPI app exposing only the projects router.

    Sidesteps W10-C Task 4's not-yet-migrated analysis routers (data/run/
    selector/clustering/explorer/static) which still use the pre-W10-B
    `Depends(get_active_analysis)` shape and crash at route-collection
    time. Once Task 4 lands these tests can switch to importing the
    full `app` from `flake_analysis.api.main`.
    """
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(projects_route.router, prefix="/api/v1")
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
async def test_create_then_list_then_get(pg_session, sample_user_factory):
    user = await sample_user_factory()
    async with await _client(pg_session, user) as client:
        r = await client.post("/api/v1/projects", json={"name": "Alpha"})
        assert r.status_code == 201, r.text
        body = r.json()
        pid = body["project_id"]
        assert body["name"] == "Alpha"

        r = await client.get("/api/v1/projects")
        assert r.status_code == 200
        names = [p["name"] for p in r.json()["projects"]]
        assert "Alpha" in names

        r = await client.get(f"/api/v1/projects/{pid}")
        assert r.status_code == 200
        assert r.json()["scan_count"] == 0


@pytest.mark.asyncio
async def test_create_dup_name_returns_409(pg_session, sample_user_factory):
    user = await sample_user_factory()
    async with await _client(pg_session, user) as client:
        await client.post("/api/v1/projects", json={"name": "dup"})
        r = await client.post("/api/v1/projects", json={"name": "dup"})
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "duplicate_project_name"


@pytest.mark.asyncio
async def test_patch_renames_project(pg_session, sample_user_factory):
    user = await sample_user_factory()
    async with await _client(pg_session, user) as client:
        r = await client.post("/api/v1/projects", json={"name": "Original"})
        pid = r.json()["project_id"]

        r = await client.patch(f"/api/v1/projects/{pid}", json={"name": "Renamed"})
        assert r.status_code == 200
        assert r.json()["name"] == "Renamed"


@pytest.mark.asyncio
async def test_delete_empty_project(pg_session, sample_user_factory):
    user = await sample_user_factory()
    async with await _client(pg_session, user) as client:
        r = await client.post("/api/v1/projects", json={"name": "tmp"})
        pid = r.json()["project_id"]

        r = await client.delete(f"/api/v1/projects/{pid}")
        assert r.status_code == 204

        r = await client.get(f"/api/v1/projects/{pid}")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_project_with_scans_returns_409(pg_session, sample_user_factory):
    user = await sample_user_factory()
    async with await _client(pg_session, user) as client:
        r = await client.post("/api/v1/projects", json={"name": "with-scan"})
        pid = r.json()["project_id"]

        # Insert a scan directly via session
        pg_session.add(
            Scan(name="s1", material="graphene", project_id=pid, created_by_id=user.id)
        )
        await pg_session.commit()

        r = await client.delete(f"/api/v1/projects/{pid}")
        assert r.status_code == 409
        body = r.json()
        assert body["error"]["code"] == "project_has_scans"
        assert body["error"]["details"]["scan_count"] == 1
