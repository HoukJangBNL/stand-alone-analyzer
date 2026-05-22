"""Usage event tests for /run endpoints (W6.4.3)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from flake_analysis.api.main import app
from flake_analysis.db.models import UsageEvent

# Dev-bypass user UUID (matches src/flake_analysis/api/auth/dev_bypass.py)
DEV_BYPASS_USER_ID = UUID("00000000-0000-0000-0000-000000000001")

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_run_thumbnails_emits_scan_run_event(
    monkeypatch, pg_session, sample_user_factory
):
    """POST /projects/{pid}/run/thumbnails emits scan_run usage event."""
    # Enable dev bypass (no need to create user; dev-bypass auto-seeds)
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "1")

    # Mock the manifest dependency to return a valid manifest
    from flake_analysis.state.manifest import Manifest

    mock_manifest = Manifest(
        analysis_folder="/tmp/analysis",
        raw_images_dir="/tmp/raw",
        annotations_path="/tmp/annotations.json",
    )

    async def mock_get_manifest(project_id: str):
        return mock_manifest

    # Mock acquire_project_lock to avoid lock contention
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_lock(project_id):
        yield

    # Mock the run_thumbnails_step to avoid actual execution
    def mock_run_thumbnails_step(*args, **kwargs):
        return {"status": "success"}

    monkeypatch.setattr(
        "flake_analysis.api.routes.run.get_manifest", mock_get_manifest
    )
    monkeypatch.setattr(
        "flake_analysis.api.routes.run.acquire_project_lock", mock_lock
    )
    monkeypatch.setattr(
        "flake_analysis.api.routes.run.run_thumbnails_step",
        mock_run_thumbnails_step,
    )

    from flake_analysis.api.deps import get_db_session

    async def _yield_pg_session():
        yield pg_session

    app.dependency_overrides[get_db_session] = _yield_pg_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/api/v1/projects/test-project/run/thumbnails",
                json={
                    "raw_ext": ".tif",
                    "quality": 85,
                    "force_recompute": False,
                },
            )
            # The response is SSE stream, so we just check it started
            assert r.status_code == 200

        # Check that a usage_events row was written with kind='scan_run'
        # (dev-bypass user_id is DEV_BYPASS_USER_ID)
        stmt = select(UsageEvent).where(UsageEvent.kind == "scan_run").where(
            UsageEvent.user_id == DEV_BYPASS_USER_ID
        )
        result = await pg_session.execute(stmt)
        rows = result.scalars().all()
        assert len(rows) >= 1, "Expected at least one scan_run event for dev-bypass user"
        # Check the most recent event
        latest = rows[-1]
        assert latest.kind == "scan_run"
        assert latest.value_json is not None
        assert "step" in latest.value_json
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_run_background_emits_scan_run_event(
    monkeypatch, pg_session, sample_user_factory
):
    """POST /projects/{pid}/run/background emits scan_run usage event."""
    # Enable dev bypass (no need to create user; dev-bypass auto-seeds)
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "1")

    # Mock the manifest dependency
    from flake_analysis.state.manifest import Manifest

    mock_manifest = Manifest(
        analysis_folder="/tmp/analysis",
        raw_images_dir="/tmp/raw",
        annotations_path="/tmp/annotations.json",
    )

    async def mock_get_manifest(project_id: str):
        return mock_manifest

    # Mock acquire_project_lock
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def mock_lock(project_id):
        yield

    # Mock run_background_step
    def mock_run_background_step(*args, **kwargs):
        return {"status": "success"}

    monkeypatch.setattr(
        "flake_analysis.api.routes.run.get_manifest", mock_get_manifest
    )
    monkeypatch.setattr(
        "flake_analysis.api.routes.run.acquire_project_lock", mock_lock
    )
    monkeypatch.setattr(
        "flake_analysis.api.routes.run.run_background_step", mock_run_background_step
    )

    from flake_analysis.api.deps import get_db_session

    async def _yield_pg_session():
        yield pg_session

    app.dependency_overrides[get_db_session] = _yield_pg_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/api/v1/projects/test-project/run/background",
                json={
                    "seed": 42,
                    "max_images": 10,
                    "gaussian_sigma": 1.5,
                    "method": "robust",
                },
            )
            assert r.status_code == 200

        # Check usage event (dev-bypass user_id is DEV_BYPASS_USER_ID)
        stmt = select(UsageEvent).where(UsageEvent.kind == "scan_run").where(
            UsageEvent.user_id == DEV_BYPASS_USER_ID
        )
        result = await pg_session.execute(stmt)
        rows = result.scalars().all()
        assert len(rows) >= 1, "Expected at least one scan_run event for dev-bypass user"
        # Check the most recent event
        latest = rows[-1]
        assert latest.kind == "scan_run"
        assert latest.value_json is not None
        assert "step" in latest.value_json
    finally:
        app.dependency_overrides.pop(get_db_session, None)
