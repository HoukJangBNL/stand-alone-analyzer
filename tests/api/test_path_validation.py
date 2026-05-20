import pytest
from httpx import AsyncClient, ASGITransport
from pathlib import Path
from flake_analysis.api.main import create_app

@pytest.mark.asyncio
async def test_validate_paths(tmp_path):
    """POST /projects/validate-paths checks existence and permissions."""
    existing_dir = tmp_path / "exists"
    existing_dir.mkdir()

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/projects/validate-paths", json={
            "analysis_folder": str(existing_dir),
            "raw_images_dir": str(tmp_path / "nonexistent"),
        })
        assert resp.status_code == 200
        body = resp.json()

        assert body["analysis_folder"]["exists"] is True
        assert body["analysis_folder"]["is_dir"] is True
        assert body["analysis_folder"]["readable"] is True

        assert body["raw_images_dir"]["exists"] is False
