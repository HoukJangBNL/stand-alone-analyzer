"""Usage event tests for /run endpoints (W6.4.3, W10-C.4b).

W10-C.4b updated the route surface to per-scan
(`/projects/{pid}/scans/{sid}/run/...`) and the usage event payload now
carries both `project_id` and `scan_id`. Tests use a mini-app exposing
only the run router because `flake_analysis.api.main` is import-broken
until W10-C.4c migrates clustering + explorer.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.logging_ctx import RequestIdMiddleware
from flake_analysis.api.routes import run as run_route
from flake_analysis.db.models import UsageEvent

# Dev-bypass user UUID (matches src/flake_analysis/api/auth/dev_bypass.py)
DEV_BYPASS_USER_ID = UUID("00000000-0000-0000-0000-000000000001")

PID = "test-project"
SID = 42

pytestmark = pytest.mark.pg


def _make_app() -> FastAPI:
    """Mini-app exposing only the run router."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(run_route.router, prefix="/api/v1")
    return app


@pytest.fixture(autouse=True)
def _clear_scan_locks():
    from flake_analysis.api import mutex
    mutex._scan_locks.clear()
    yield
    mutex._scan_locks.clear()


@pytest.mark.asyncio
async def test_run_thumbnails_emits_scan_run_event(
    monkeypatch, pg_session, sample_user_factory
):
    """POST /projects/{pid}/scans/{sid}/run/thumbnails emits scan_run usage event."""
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "1")

    # Stub out the manifest dependency to skip filesystem access.
    from flake_analysis.state.manifest import Manifest

    mock_manifest = Manifest(
        analysis_folder="/tmp/analysis",
        raw_images_dir="/tmp/raw",
        annotations_path="/tmp/annotations.json",
    )

    async def mock_get_manifest(project_id: str, scan_id: int):
        return mock_manifest

    @asynccontextmanager
    async def mock_lock(scan_id):
        yield

    def mock_run_thumbnails_step(*args, **kwargs):
        return {"status": "success"}

    monkeypatch.setattr(
        "flake_analysis.api.routes.run.get_manifest", mock_get_manifest
    )
    monkeypatch.setattr(
        "flake_analysis.api.routes.run.acquire_scan_lock", mock_lock
    )
    monkeypatch.setattr(
        "flake_analysis.api.routes.run.run_thumbnails_step",
        mock_run_thumbnails_step,
    )

    from flake_analysis.api.deps import get_db_session

    async def _yield_pg_session():
        yield pg_session

    app = _make_app()
    app.dependency_overrides[get_db_session] = _yield_pg_session

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post(
            f"/api/v1/projects/{PID}/scans/{SID}/run/thumbnails",
            json={
                "raw_ext": ".tif",
                "quality": 85,
                "force_recompute": False,
            },
        )
        # The response is SSE stream, so we just check it started.
        assert r.status_code == 200

    # Check that a usage_events row was written with kind='scan_run'
    # (dev-bypass user_id is DEV_BYPASS_USER_ID).
    stmt = (
        select(UsageEvent)
        .where(UsageEvent.kind == "scan_run")
        .where(UsageEvent.user_id == DEV_BYPASS_USER_ID)
    )
    result = await pg_session.execute(stmt)
    rows = result.scalars().all()
    assert len(rows) >= 1, "Expected at least one scan_run event for dev-bypass user"
    latest = rows[-1]
    assert latest.kind == "scan_run"
    assert latest.value_json is not None
    assert latest.value_json.get("step") == "thumbnails"
    assert latest.value_json.get("project_id") == PID
    assert latest.value_json.get("scan_id") == SID


@pytest.mark.asyncio
async def test_run_background_emits_scan_run_event(
    monkeypatch, pg_session, sample_user_factory
):
    """POST /projects/{pid}/scans/{sid}/run/background emits scan_run usage event."""
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "1")

    from flake_analysis.state.manifest import Manifest

    mock_manifest = Manifest(
        analysis_folder="/tmp/analysis",
        raw_images_dir="/tmp/raw",
        annotations_path="/tmp/annotations.json",
    )

    async def mock_get_manifest(project_id: str, scan_id: int):
        return mock_manifest

    @asynccontextmanager
    async def mock_lock(scan_id):
        yield

    def mock_run_background_step(*args, **kwargs):
        return {"status": "success"}

    monkeypatch.setattr(
        "flake_analysis.api.routes.run.get_manifest", mock_get_manifest
    )
    monkeypatch.setattr(
        "flake_analysis.api.routes.run.acquire_scan_lock", mock_lock
    )
    monkeypatch.setattr(
        "flake_analysis.api.routes.run.run_background_step", mock_run_background_step
    )

    from flake_analysis.api.deps import get_db_session

    async def _yield_pg_session():
        yield pg_session

    app = _make_app()
    app.dependency_overrides[get_db_session] = _yield_pg_session

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as c:
        r = await c.post(
            f"/api/v1/projects/{PID}/scans/{SID}/run/background",
            json={
                "seed": 42,
                "max_images": 10,
                "gaussian_sigma": 1.5,
                "method": "robust",
            },
        )
        assert r.status_code == 200

    stmt = (
        select(UsageEvent)
        .where(UsageEvent.kind == "scan_run")
        .where(UsageEvent.user_id == DEV_BYPASS_USER_ID)
    )
    result = await pg_session.execute(stmt)
    rows = result.scalars().all()
    assert len(rows) >= 1, "Expected at least one scan_run event for dev-bypass user"
    latest = rows[-1]
    assert latest.kind == "scan_run"
    assert latest.value_json is not None
    assert latest.value_json.get("step") == "background"
    assert latest.value_json.get("project_id") == PID
    assert latest.value_json.get("scan_id") == SID
