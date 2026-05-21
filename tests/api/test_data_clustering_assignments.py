from pathlib import Path

import pandas as pd
import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


@pytest.mark.asyncio
async def test_assignments_404_when_missing(tmp_path: Path):
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/v1/projects/local/data/clustering/assignments")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "clustering_not_fitted"


@pytest.mark.asyncio
async def test_assignments_returns_json(tmp_path: Path):
    (tmp_path / "04_clustering").mkdir(parents=True)
    df = pd.DataFrame({
        "domain_id": [1, 2, 3],
        "cluster_label": [0, 1, -1],
        "max_posterior": [0.9, 0.8, 0.4],
    })
    df.to_parquet(tmp_path / "04_clustering" / "assignments.parquet", index=False)

    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/v1/projects/local/data/clustering/assignments")
    assert r.status_code == 200
    body = r.json()
    assert body["domain_id"] == [1, 2, 3]
    assert body["cluster_label"] == [0, 1, -1]
