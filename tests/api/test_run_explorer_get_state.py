import json
from pathlib import Path

import pandas as pd
import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


def _seed_with_saved_state(folder: Path) -> None:
    (folder / "04_clustering").mkdir(parents=True)
    (folder / "05_domain_proximity").mkdir(parents=True)
    (folder / "06_explorer").mkdir(parents=True)
    (folder / "06_explorer" / "explorer_state.json").write_text(json.dumps({
        "include_labels": ["thin"],
        "exclude_labels": [],
        "neighbor_filter": {"size_enabled": True, "size_min": 1, "size_max": 50,
                            "isolate_enabled": False, "d_isolate_px": 80.0,
                            "exclude_border": False},
        "saved_at": "2026-05-21T00:00:00Z",
    }))
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(folder / "raw"),
        "steps": {
            "explorer": {"completed_at": "2026-05-21T00:00:00Z", "params": {},
                         "params_hash": "eh", "input_hashes": {}, "outputs": {}},
        },
    }))


@pytest.mark.asyncio
async def test_get_state_returns_saved_payload(tmp_path: Path):
    _seed_with_saved_state(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/run/explorer/state")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["include_labels"] == ["thin"]
    assert payload["neighbor_filter"]["size_min"] == 1


@pytest.mark.asyncio
async def test_get_state_404_when_unsaved(tmp_path: Path):
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(tmp_path),
        "raw_images_dir": str(tmp_path / "raw"),
        "steps": {},
    }))
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/run/explorer/state")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "explorer_state_missing"
