import json
from pathlib import Path

import pandas as pd
import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


def _seed_clustering_world(folder: Path) -> None:
    (folder / "04_clustering").mkdir(parents=True)
    (folder / "05_domain_proximity").mkdir(parents=True)
    (folder / "04_clustering" / "labels.json").write_text(json.dumps({
        "version": 1, "n_clusters": 2,
        "groups": [
            {"id": 0, "name": "thin", "size": 3, "mean_rgb": [0, 0, 0]},
            {"id": 1, "name": "thick", "size": 2, "mean_rgb": [0, 0, 0]},
        ],
        "assignments": {"10": 0, "11": 0, "12": 1},
        "thresholds": {"0": 0.5, "1": 0.5},
        "noise_label": -1, "random_state": 42, "fitted_at": "x",
    }))
    pd.DataFrame({
        "domain_id": [10, 11, 12],
        "cluster_id": [0, 0, 1],
        "posterior_p": [0.9, 0.8, 0.85],
    }).to_parquet(folder / "04_clustering" / "assignments.parquet", index=False)
    pd.DataFrame({
        "domain_id": [10, 11, 12],
        "flake_id":  [100, 100, 100],
        "flake_size": [3, 3, 3],
        "image_id":  [7, 7, 7],
    }).to_parquet(folder / "05_domain_proximity" / "flake_assignments.parquet", index=False)
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(folder / "raw"),
        "steps": {}, "image_id_to_stem": {},
    }))


@pytest.mark.asyncio
async def test_flake_detail_returns_domain_ids_and_cluster_names(tmp_path: Path):
    _seed_clustering_world(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/explorer/flake/100")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["flake_id"] == 100
    assert payload["image_id"] == 7
    assert payload["domain_ids"] == [10, 11, 12]
    assert set(payload["cluster_names"]) == {"thin", "thick"}


@pytest.mark.asyncio
async def test_flake_detail_404_when_unknown(tmp_path: Path):
    _seed_clustering_world(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/explorer/flake/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_flake_detail_404_when_clustering_missing(tmp_path: Path):
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(tmp_path),
        "raw_images_dir": str(tmp_path / "raw"),
        "steps": {}, "image_id_to_stem": {},
    }))
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/projects/local/explorer/flake/100")
    assert resp.status_code == 404
