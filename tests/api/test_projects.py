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


@pytest.mark.asyncio
async def test_post_empty_body_creates_default_project(tmp_path):
    """POST /api/v1/projects with no body falls back to SAA_ANALYSIS_FOLDER."""
    os.environ["SAA_ANALYSIS_FOLDER"] = str(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # No json= kwarg → httpx sends an empty request body.
            resp = await client.post("/api/v1/projects")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["project_id"] == "local"
            assert body["analysis_folder"] == str(tmp_path)
            assert body["raw_images_dir"] is None
            assert body["annotations_path"] is None
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_post_empty_json_object_creates_default_project(tmp_path):
    """POST /api/v1/projects with body {} also falls back to SAA_ANALYSIS_FOLDER."""
    os.environ["SAA_ANALYSIS_FOLDER"] = str(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/v1/projects", json={})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["project_id"] == "local"
            assert body["analysis_folder"] == str(tmp_path)
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_post_with_full_body_still_works(tmp_path):
    """POST /api/v1/projects with all 3 paths still returns those paths verbatim."""
    analysis = tmp_path / "a"
    raw = tmp_path / "raw"
    ann = tmp_path / "ann.json"
    analysis.mkdir()
    raw.mkdir()
    ann.write_text("{}")

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/projects",
            json={
                "analysis_folder": str(analysis),
                "raw_images_dir": str(raw),
                "annotations_path": str(ann),
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["project_id"] == "local"
        assert body["analysis_folder"] == str(analysis)
        assert body["raw_images_dir"] == str(raw)
        assert body["annotations_path"] == str(ann)
