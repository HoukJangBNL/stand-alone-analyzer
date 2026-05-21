import json
from pathlib import Path

import pandas as pd
import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


def _write_minimal_clustering(folder: Path) -> None:
    (folder / "04_clustering").mkdir(parents=True)
    df = pd.DataFrame({
        "domain_id": [1, 2, 3],
        "cluster_label": [0, 1, 0],
        "max_posterior": [0.9, 0.8, 0.4],
    })
    df.to_parquet(folder / "04_clustering" / "assignments.parquet", index=False)
    labels = {
        "version": 1,
        "n_clusters": 2,
        "groups": [
            {"id": 0, "name": "a", "size": 2, "mean_rgb": [0, 0, 0]},
            {"id": 1, "name": "b", "size": 1, "mean_rgb": [0, 0, 0]},
        ],
        "assignments": {"1": 0, "2": 1, "3": 0},
        "thresholds": {"0": 0.5, "1": 0.5},
        "noise_label": -1,
        "random_state": 42,
        "fitted_at": "2026-05-21T00:00:00Z",
    }
    (folder / "04_clustering" / "labels.json").write_text(json.dumps(labels))


@pytest.mark.asyncio
async def test_apply_thresholds_streams_error_without_clustering(tmp_path: Path):
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    body = {"cluster_thresholds": {0: 0.7}}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream(
            "POST", "/api/v1/projects/local/run/clustering/apply_thresholds", json=body
        ) as r:
            assert r.status_code == 200
            text = ""
            async for chunk in r.aiter_text():
                text += chunk
            assert "error" in text


@pytest.mark.asyncio
async def test_apply_thresholds_streams_done_with_summary(tmp_path: Path):
    _write_minimal_clustering(tmp_path)
    # apply_thresholds also reads/writes manifest.json — write a minimal one.
    (tmp_path / "manifest.json").write_text(json.dumps({"version": 1, "steps": {}}))

    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    body = {"cluster_thresholds": {0: 0.5, 1: 0.5}}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream(
            "POST", "/api/v1/projects/local/run/clustering/apply_thresholds", json=body
        ) as r:
            assert r.status_code == 200
            text = ""
            async for chunk in r.aiter_text():
                text += chunk
            assert "done" in text
            assert "n_pass" in text
