"""W2.4 — manifest endpoint DB rewire tests.

Three-case suite (per plan pinned decision #6):
  (a) no DB row -> endpoint returns disk manifest unchanged (pure-Python)
  (b) DB row present -> endpoint overlays steps_done and exposes status
  (c) DB session error -> endpoint returns 500 with documented envelope

Post-W10-B `get_manifest` and `get_active_analysis` are plain async functions
called directly inside the route (not FastAPI Depends), so we monkeypatch the
imports in `flake_analysis.api.routes.data` instead of using
`app.dependency_overrides`. Auth + DB session deps remain FastAPI deps and
are overridden at the app level.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.main import app
from flake_analysis.api.schemas.data import ManifestModel
from flake_analysis.state.manifest import Manifest, StepEntry


def test_manifest_model_has_optional_status_field():
    m = ManifestModel.model_validate({
        "version": 1,
        "steps": {},
    })
    assert m.status is None

    m2 = ManifestModel.model_validate({
        "version": 1,
        "steps": {},
        "status": "running",
    })
    assert m2.status == "running"


def _bypass_db_and_auth():
    """Override the FastAPI Depends used by the manifest endpoint.

    The route still resolves `get_db_session` (Depends) and `get_current_user`
    (Depends); the actual `get_manifest`/`get_active_analysis` calls are
    plain function calls patched in each test via monkeypatch.
    """
    from flake_analysis.api.auth import get_current_user
    from flake_analysis.api.deps import get_db_session

    async def _fake_session():
        yield None

    app.dependency_overrides[get_db_session] = _fake_session
    app.dependency_overrides[get_current_user] = lambda: object()


@pytest.mark.asyncio
async def test_endpoint_returns_disk_manifest_when_no_db_row(monkeypatch):
    m = Manifest(version=1, steps={
        "background": StepEntry(completed_at="2026-05-01T00:00:00Z"),
    })

    async def _fake_get_manifest(project_id, scan_id):
        return m

    async def _fake_get_active_analysis(scan_id, session):
        return None

    monkeypatch.setattr(
        "flake_analysis.api.routes.data.get_manifest", _fake_get_manifest
    )
    monkeypatch.setattr(
        "flake_analysis.api.routes.data.get_active_analysis",
        _fake_get_active_analysis,
    )
    _bypass_db_and_auth()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/projects/local/scans/1/data/manifest")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] is None
            assert body["steps"]["background"]["completed_at"] == "2026-05-01T00:00:00Z"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_endpoint_overlays_db_steps_and_sets_status(monkeypatch):
    m = Manifest(version=1, steps={})

    class FakeAnalysis:
        steps_done = {"background": True}

        class _S:
            value = "running"

        status = _S()

    async def _fake_get_manifest(project_id, scan_id):
        return m

    async def _fake_get_active_analysis(scan_id, session):
        return FakeAnalysis()

    monkeypatch.setattr(
        "flake_analysis.api.routes.data.get_manifest", _fake_get_manifest
    )
    monkeypatch.setattr(
        "flake_analysis.api.routes.data.get_active_analysis",
        _fake_get_active_analysis,
    )
    _bypass_db_and_auth()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/projects/local/scans/1/data/manifest")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "running"
            assert "background" in body["steps"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_endpoint_returns_500_db_unavailable_on_session_error(monkeypatch):
    from flake_analysis.api.errors import DbUnavailable

    m = Manifest(version=1, steps={})

    async def _fake_get_manifest(project_id, scan_id):
        return m

    async def _boom(scan_id, session):
        raise DbUnavailable()

    monkeypatch.setattr(
        "flake_analysis.api.routes.data.get_manifest", _fake_get_manifest
    )
    monkeypatch.setattr(
        "flake_analysis.api.routes.data.get_active_analysis", _boom
    )
    _bypass_db_and_auth()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/projects/local/scans/1/data/manifest")
            assert r.status_code == 500
            err = r.json()["error"]
            assert err["code"] == "db_unavailable"
    finally:
        app.dependency_overrides.clear()
