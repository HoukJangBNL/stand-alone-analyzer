"""Integration test for get_current_user drop-in dependency (W6.2.3).

Tests that the new Cognito-backed get_current_user works as a drop-in
replacement for the old stub by exercising an existing protected route
(/api/v1/projects/active) with a valid bearer token.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.main import app

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_protected_route_accepts_valid_token(signed_token, pg_session):
    """Protected route accepts valid bearer token and upserts user."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(
            "/api/v1/projects/active",
            headers={"Authorization": f"Bearer {signed_token}"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["project_id"] == "local"


@pytest.mark.asyncio
async def test_protected_route_rejects_missing_token(monkeypatch, pg_session):
    """Protected route rejects request without bearer token."""
    monkeypatch.delenv("SAA_AUTH_DEV_BYPASS", raising=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/projects/active")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_rejects_invalid_token(monkeypatch, pg_session):
    """Protected route rejects malformed bearer token."""
    monkeypatch.delenv("SAA_AUTH_DEV_BYPASS", raising=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(
            "/api/v1/projects/active",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert r.status_code == 401
