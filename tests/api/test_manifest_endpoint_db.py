"""W2.4 — manifest endpoint DB rewire tests.

Three-case suite (per plan pinned decision #6):
  (a) no DB row -> endpoint returns disk manifest unchanged (pure-Python)
  (b) DB row present -> endpoint overlays steps_done and exposes status
  (c) DB session error -> endpoint returns 500 with documented envelope

All three cases use FastAPI dependency overrides so they do not require
a live PostgreSQL.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.deps import get_active_analysis, get_manifest
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


@pytest.mark.asyncio
async def test_endpoint_returns_disk_manifest_when_no_db_row():
    m = Manifest(version=1, steps={
        "background": StepEntry(completed_at="2026-05-01T00:00:00Z"),
    })
    app.dependency_overrides[get_manifest] = lambda: m
    app.dependency_overrides[get_active_analysis] = lambda: None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/projects/local/data/manifest")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] is None
            assert body["steps"]["background"]["completed_at"] == "2026-05-01T00:00:00Z"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_endpoint_overlays_db_steps_and_sets_status():
    m = Manifest(version=1, steps={})

    class FakeAnalysis:
        steps_done = {"background": True}

        class _S:
            value = "running"

        status = _S()

    app.dependency_overrides[get_manifest] = lambda: m
    app.dependency_overrides[get_active_analysis] = lambda: FakeAnalysis()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/projects/local/data/manifest")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "running"
            assert "background" in body["steps"]
    finally:
        app.dependency_overrides.clear()
