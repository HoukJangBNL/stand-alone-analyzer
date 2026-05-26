"""W10 acceptance gate — direct coverage of D2/D4/D6.

These specs are intentionally chunky integration tests; if they break, the W10
contract has regressed and someone needs to look hard. Each spec runs against a
real PG via the `pg_session` fixture from conftest plus router-only mini-apps
(matching the `tests/api/test_projects.py::_client` pattern).

Plan adjustment (vs `docs/superpowers/plans/2026-05-22-W10-E-test-and-data-migration.md`):
- D4 originally referenced `POST /api/v1/projects/{pid}/scans/{sid}/run/fake`.
  No such production route exists post-W10-C — `run.py` only registers
  thumbnails/background/domain_stats/domain_proximity, all of which require a
  full manifest + raw image directory. To keep D4 a pure mutex contract test
  (the point of D4), we mount a synthetic `/run/fake` endpoint on a test app
  that holds the real `acquire_scan_lock(scan_id)` for the request lifetime.
  This proves per-scan isolation end-to-end without coupling the canary to
  arbitrary pipeline plumbing.
- ProjectBusy.status_code is 423 (HTTP_423_LOCKED), not 409 as the plan draft
  said. The contention assertion uses 423 accordingly.
- POST /projects returns ProjectHandle (no `scan_count` field); D6 asserts on
  `name` instead of the plan's `scan_count == 0` check.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.routes import projects as projects_route

pytestmark = pytest.mark.pg


def _make_projects_app() -> FastAPI:
    """Mini-app exposing only the projects router (matches test_projects.py)."""
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(projects_route.router, prefix="/api/v1")
    return app


async def _projects_client(pg_session, current_user):
    """Wire get_db_session + get_current_user to test fixtures."""
    from flake_analysis.api.auth import get_current_user
    from flake_analysis.api.deps import get_db_session

    app = _make_projects_app()

    async def _override_db():
        yield pg_session

    app.dependency_overrides[get_db_session] = _override_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ----- D2: DELETE-RESTRICT --------------------------------------------------
@pytest.mark.asyncio
async def test_delete_project_with_scans_returns_409(
    pg_session, sample_user_factory, sample_project_factory, sample_scan_factory,
):
    """D2: a project with at least one scan must NOT be deletable."""
    user = await sample_user_factory()
    project = await sample_project_factory(owner=user)
    await sample_scan_factory(project=project, name="s1")
    await pg_session.commit()

    async with await _projects_client(pg_session, user) as client:
        resp = await client.delete(f"/api/v1/projects/{project.id}")
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["error"]["code"] == "project_has_scans"
        assert body["error"]["details"]["scan_count"] >= 1


@pytest.mark.asyncio
async def test_delete_project_after_clearing_scans_returns_204(
    pg_session, sample_user_factory, sample_project_factory, sample_scan_factory,
):
    """D2 inverse: once scans are gone, delete works."""
    user = await sample_user_factory()
    project = await sample_project_factory(owner=user)
    scan = await sample_scan_factory(project=project, name="s1")
    await pg_session.commit()

    # Drop the only scan via ORM; mimics manual cleanup.
    await pg_session.delete(scan)
    await pg_session.commit()

    async with await _projects_client(pg_session, user) as client:
        resp = await client.delete(f"/api/v1/projects/{project.id}")
        assert resp.status_code == 204


# ----- D4: per-scan mutex isolation ----------------------------------------
def _make_fake_run_app() -> FastAPI:
    """Mini-app with a synthetic per-scan run endpoint that holds the real
    `acquire_scan_lock(scan_id)` for ~150ms.

    Mirrors the W10-C URL grammar (`/projects/{pid}/scans/{sid}/run/fake`) so
    the contention shape is identical to a real run endpoint, but the work is
    a sleep — no manifest, no pipeline, no DB. This isolates D4 to the mutex
    contract.
    """
    from flake_analysis.api.mutex import acquire_scan_lock

    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    router = APIRouter()

    @router.post("/projects/{project_id}/scans/{scan_id}/run/fake")
    async def run_fake(project_id: str, scan_id: int):
        async with acquire_scan_lock(scan_id):
            await asyncio.sleep(0.15)
            return {"ok": True, "scan_id": scan_id}

    app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture(autouse=True)
def _clear_scan_locks_per_test():
    """Ensure D4 specs start with a clean lock registry."""
    from flake_analysis.api import mutex
    mutex._scan_locks.clear()
    yield
    mutex._scan_locks.clear()


@pytest.mark.asyncio
async def test_two_scans_run_concurrently_no_contention(pg_session):
    """D4: different scans (even logically in the same project) share NO lock."""
    app = _make_fake_run_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        async def run_one(sid: int) -> int:
            r = await c.post(f"/api/v1/projects/p-d4/scans/{sid}/run/fake")
            return r.status_code

        codes = await asyncio.gather(run_one(101), run_one(102))
        assert codes == [200, 200], (
            f"expected per-scan isolation (both succeed), got {codes}"
        )


@pytest.mark.asyncio
async def test_same_scan_concurrent_runs_one_busy(pg_session):
    """D4: two concurrent runs on the SAME scan — one wins, the other 423s.

    Note: `ProjectBusy.status_code = HTTP_423_LOCKED`, so the contention
    response is 423 (the plan draft said 409 but that contradicts errors.py).
    """
    app = _make_fake_run_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        async def run_one() -> int:
            r = await c.post("/api/v1/projects/p-d4/scans/202/run/fake")
            return r.status_code

        codes = await asyncio.gather(run_one(), run_one())
        assert sorted(codes) == [200, 423], (
            f"expected one success + one busy, got {codes}"
        )


# ----- D6: force-create-project UX, backend side ---------------------------
@pytest.mark.asyncio
async def test_fresh_user_lists_zero_projects(pg_session, sample_user_factory):
    """D6: a user with no projects gets an empty list (not a 404)."""
    user = await sample_user_factory()
    await pg_session.commit()

    async with await _projects_client(pg_session, user) as client:
        resp = await client.get("/api/v1/projects")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"projects": []}


@pytest.mark.asyncio
async def test_fresh_user_can_create_first_project(pg_session, sample_user_factory):
    """D6: no prior state required — POST works on day zero."""
    user = await sample_user_factory()
    await pg_session.commit()

    async with await _projects_client(pg_session, user) as client:
        resp = await client.post(
            "/api/v1/projects",
            json={"name": "first-project", "description": "hello"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "first-project"
        assert body["description"] == "hello"
        assert body["owner_id"] == str(user.id)
        assert "project_id" in body
