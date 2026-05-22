"""W5-B1.2 — GET/POST /materials route tests."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.deps import get_db_session
from flake_analysis.api.main import app

pytestmark = pytest.mark.pg


def _override(pg_session):
    async def _yield():
        yield pg_session
    app.dependency_overrides[get_db_session] = _yield


@pytest.mark.asyncio
async def test_list_materials_includes_seed(pg_session):
    """GET /materials returns at least the W5-A seed rows alphabetically."""
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/v1/materials")
            assert r.status_code == 200
            names = [m["name"] for m in r.json()["materials"]]
            for expected in ["MoS2", "WS2", "WSe2", "graphene", "hBN"]:
                assert expected in names
            # PG default collation is locale-aware (case-insensitive grouping);
            # Python's plain sorted() is binary/ASCII (uppercase before
            # lowercase). Compare case-insensitively to assert "alphabetical"
            # in the locale sense.
            assert names == sorted(names, key=str.lower)
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_post_materials_creates_new(pg_session):
    """POST /materials with a fresh name returns created=True."""
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/v1/materials", json={"name": "  Si  "})
            assert r.status_code == 200
            body = r.json()
            assert body == {"name": "si", "created": True}
            # Second call is idempotent
            r2 = await c.post("/api/v1/materials", json={"name": "SI"})
            assert r2.json() == {"name": "si", "created": False}
    finally:
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_post_materials_rejects_blank(pg_session):
    _override(pg_session)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/v1/materials", json={"name": "   "})
            assert r.status_code == 422
    finally:
        app.dependency_overrides.pop(get_db_session, None)
