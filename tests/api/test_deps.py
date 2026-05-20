# tests/api/test_deps.py
import pytest
import os
from pathlib import Path
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from flake_analysis.api.deps import get_manifest
from flake_analysis.state.manifest import Manifest, save_manifest

def test_get_manifest_dependency(tmp_path):
    """get_manifest loads manifest for project_id 'local' from analysis_folder."""
    analysis_folder = tmp_path / "analysis"
    analysis_folder.mkdir()

    m = Manifest(analysis_folder=str(analysis_folder))
    save_manifest(m, analysis_folder)

    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis_folder)

    app = FastAPI()

    @app.get("/test/{project_id}/manifest")
    async def test_route(manifest: Manifest = Depends(get_manifest)):
        return {"version": manifest.version}

    try:
        client = TestClient(app)
        resp = client.get("/test/local/manifest")
        assert resp.status_code == 200
        assert resp.json()["version"] == 1
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)
