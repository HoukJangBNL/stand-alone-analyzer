import pytest
import os
from httpx import AsyncClient, ASGITransport
from flake_analysis.api.main import create_app

@pytest.mark.asyncio
async def test_create_project(tmp_path):
    """POST /projects creates a project handle."""
    analysis_folder = tmp_path / "proj1"
    analysis_folder.mkdir()

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/projects", json={
            "analysis_folder": str(analysis_folder),
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["project_id"] == "local"
        assert body["analysis_folder"] == str(analysis_folder)

@pytest.mark.asyncio
async def test_get_active_project(tmp_path):
    """GET /projects/active returns the active project."""
    os.environ["SAA_ANALYSIS_FOLDER"] = str(tmp_path)

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/projects/active")
        assert resp.status_code == 200
        body = resp.json()
        assert body["project_id"] == "local"

    os.environ.pop("SAA_ANALYSIS_FOLDER", None)
