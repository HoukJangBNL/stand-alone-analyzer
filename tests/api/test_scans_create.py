"""W5-B1.3 — POST /projects/{pid}/scans tests."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from flake_analysis.api.deps import get_db_session
from flake_analysis.api.main import app
from flake_analysis.db.models import Material, Scan

pytestmark = pytest.mark.pg


def _override(pg_session):
    async def _yield():
        yield pg_session
    app.dependency_overrides[get_db_session] = _yield


@pytest.mark.asyncio
async def test_create_scan_with_known_material(
    pg_session, sample_user_factory, sample_project_factory,
):
    user = await sample_user_factory()
    project = await sample_project_factory(owner=user)
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                f"/api/v1/projects/{project.id}/scans",
                json={
                    "name": "scan_2026_05_22_a",
                    "material": "graphene",
                    "image_count": 100,
                    "extra_metadata": {"microscope": "Olympus BX53M"},
                },
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["material"] == "graphene"
            assert body["image_count"] == 100
            assert body["extra_metadata"] == {"microscope": "Olympus BX53M"}
            assert isinstance(body["scan_id"], int)
            # Verify in DB
            row = (await pg_session.execute(
                select(Scan).where(Scan.id == body["scan_id"])
            )).scalar_one()
            assert row.created_by_id is not None
            assert row.project_id == project.id
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_create_scan_auto_adds_material(
    pg_session, sample_user_factory, sample_project_factory,
):
    """Unknown material is normalized + inserted, then scan binds to it."""
    user = await sample_user_factory()
    project = await sample_project_factory(owner=user)
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                f"/api/v1/projects/{project.id}/scans",
                json={
                    "name": "scan_b",
                    "material": "  NewMat  ",
                    "image_count": 5,
                },
            )
            assert r.status_code == 201
            assert r.json()["material"] == "newmat"
            mat = (await pg_session.execute(
                select(Material).where(Material.name == "newmat")
            )).scalar_one()
            assert mat is not None
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_create_scan_rejects_zero_image_count(
    pg_session, sample_user_factory, sample_project_factory,
):
    user = await sample_user_factory()
    project = await sample_project_factory(owner=user)
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                f"/api/v1/projects/{project.id}/scans",
                json={"name": "x", "material": "graphene", "image_count": 0},
            )
            assert r.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_create_scan_404_for_unknown_project(pg_session):
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/api/v1/projects/does-not-exist/scans",
                json={"name": "x", "material": "graphene", "image_count": 1},
            )
            assert r.status_code == 404
            assert r.json()["error"]["code"] == "project_not_found"
    finally:
        app.dependency_overrides.pop(get_db_session, None)
