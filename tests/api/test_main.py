import pytest
from httpx import AsyncClient, ASGITransport
from flake_analysis.api.main import create_app

@pytest.mark.asyncio
async def test_app_factory():
    """App factory returns FastAPI instance with routes mounted."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

@pytest.mark.asyncio
async def test_cors_disabled_by_default():
    """CORS middleware not added when allowed_origins is empty."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/health")
        assert "access-control-allow-origin" not in resp.headers
