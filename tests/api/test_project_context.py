"""Tests for ProjectContext dependency (deps.get_project_context)."""
from __future__ import annotations
import os
import pytest
from fastapi import FastAPI, Depends
from httpx import ASGITransport, AsyncClient


def test_project_context_dataclass_has_project_id_and_analysis_folder():
    """ProjectContext is a frozen dataclass with two str fields."""
    from flake_analysis.api.deps import ProjectContext

    ctx = ProjectContext(project_id="local", analysis_folder="/tmp/x")
    assert ctx.project_id == "local"
    assert ctx.analysis_folder == "/tmp/x"


def test_project_context_is_frozen():
    """ProjectContext instances cannot be mutated after construction."""
    from dataclasses import FrozenInstanceError
    from flake_analysis.api.deps import ProjectContext

    ctx = ProjectContext(project_id="local", analysis_folder="/tmp/x")
    with pytest.raises(FrozenInstanceError):
        ctx.project_id = "other"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_get_project_context_reads_path_param(tmp_path):
    """get_project_context picks up project_id from the path parameter."""
    os.environ["SAA_ANALYSIS_FOLDER"] = str(tmp_path)
    try:
        from flake_analysis.api.deps import ProjectContext, get_project_context

        app = FastAPI()

        @app.get("/projects/{project_id}/probe")
        async def probe(ctx: ProjectContext = Depends(get_project_context)):
            return {"project_id": ctx.project_id, "analysis_folder": ctx.analysis_folder}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/projects/local/probe")
            assert resp.status_code == 200
            body = resp.json()
            assert body["project_id"] == "local"
            assert body["analysis_folder"] == str(tmp_path)
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_get_project_context_defaults_when_no_path_param(tmp_path):
    """When the route has no {project_id} parameter, project_id defaults to 'local'."""
    os.environ["SAA_ANALYSIS_FOLDER"] = str(tmp_path)
    try:
        from flake_analysis.api.deps import ProjectContext, get_project_context

        app = FastAPI()

        @app.get("/probe")
        async def probe(ctx: ProjectContext = Depends(get_project_context)):
            return {"project_id": ctx.project_id, "analysis_folder": ctx.analysis_folder}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/probe")
            assert resp.status_code == 200
            body = resp.json()
            assert body["project_id"] == "local"
            assert body["analysis_folder"] == str(tmp_path)
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_get_project_context_does_not_mutate_active_project():
    """get_project_context is read-only — must not touch deps._active_project."""
    import flake_analysis.api.deps as deps_module
    deps_module._active_project = "/custom/folder"

    from flake_analysis.api.deps import get_project_context

    app = FastAPI()

    @app.get("/projects/{project_id}/probe")
    async def probe(ctx=Depends(get_project_context)):
        return {"folder": ctx.analysis_folder}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/projects/local/probe")
        assert resp.status_code == 200
        assert resp.json()["folder"] == "/custom/folder"

    # _active_project must still be exactly the value the test seeded.
    assert deps_module._active_project == "/custom/folder"
