import json
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


@pytest.mark.asyncio
async def test_get_clustering_labels_404_when_missing(tmp_path: Path, monkeypatch):
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))

    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/v1/projects/local/data/clustering/labels")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "clustering_not_fitted"


@pytest.mark.asyncio
async def test_get_clustering_labels_returns_json(tmp_path: Path, monkeypatch):
    (tmp_path / "04_clustering").mkdir(parents=True)
    payload = {
        "version": 1,
        "n_clusters": 1,
        "groups": [{"id": 0, "name": "a", "size": 3, "mean_rgb": [0.1, 0.2, 0.3]}],
        "assignments": {"1": 0, "2": 0, "3": 0},
        "thresholds": {"0": 0.5},
        "noise_label": -1,
        "random_state": 42,
        "fitted_at": "2026-05-21T00:00:00Z",
    }
    (tmp_path / "04_clustering" / "labels.json").write_text(json.dumps(payload))

    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))

    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/v1/projects/local/data/clustering/labels")
    assert r.status_code == 200
    body = r.json()
    assert body["n_clusters"] == 1
    assert body["thresholds"]["0"] == 0.5
