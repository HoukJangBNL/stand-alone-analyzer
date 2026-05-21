"""save_state is SYNCHRONOUS JSON (pinned decision #12) — NOT SSE."""
import json
from pathlib import Path

import pandas as pd
import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


def _seed_for_save(folder: Path) -> None:
    """Need clustering+proximity steps committed for save_explorer_state to succeed."""
    (folder / "04_clustering").mkdir(parents=True)
    (folder / "05_domain_proximity").mkdir(parents=True)
    (folder / "04_clustering" / "labels.json").write_text(json.dumps({
        "version": 1, "n_clusters": 1,
        "groups": [{"id": 0, "name": "thin", "size": 1, "mean_rgb": [0, 0, 0]}],
        "assignments": {"10": 0}, "thresholds": {"0": 0.5},
        "noise_label": -1, "random_state": 42, "fitted_at": "x",
    }))
    pd.DataFrame({"domain_id": [10], "cluster_id": [0], "posterior_p": [0.9]}).to_parquet(
        folder / "04_clustering" / "assignments.parquet", index=False)
    pd.DataFrame({
        "domain_id": [10], "flake_id": [100], "flake_size": [1], "image_id": [0]
    }).to_parquet(folder / "05_domain_proximity" / "flake_assignments.parquet", index=False)
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(folder / "raw"),
        "steps": {
            "clustering": {"completed_at": "x", "params": {}, "params_hash": "ch",
                           "input_hashes": {}, "outputs": {}},
            "domain_proximity": {"completed_at": "x", "params": {}, "params_hash": "ph",
                                 "input_hashes": {}, "outputs": {}},
        },
    }))


@pytest.mark.asyncio
async def test_save_state_returns_json_200_not_sse(tmp_path: Path):
    _seed_for_save(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    body = {
        "include_labels": ["thin"],
        "exclude_labels": [],
        "neighbor_filter": {"size_min": 1, "size_max": 50,
                            "isolation_min": None, "exclude_border_clipped": False},
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/projects/local/run/explorer/save_state", json=body)

    assert resp.status_code == 200
    # Must NOT be SSE — content-type stays application/json
    assert resp.headers.get("content-type", "").startswith("application/json")
    payload = resp.json()
    assert "state_path" in payload
    assert payload["state_path"].endswith("explorer_state.json")
    assert payload["selected_count"] is None  # no selected_flake_ids in body


@pytest.mark.asyncio
async def test_save_state_persists_selected_flake_ids(tmp_path: Path):
    _seed_for_save(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    body = {
        "include_labels": [],
        "exclude_labels": [],
        "neighbor_filter": {"size_min": None, "size_max": None,
                            "isolation_min": None, "exclude_border_clipped": False},
        "selected_flake_ids": [100, 200, 300],
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/projects/local/run/explorer/save_state", json=body)
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["selected_count"] == 3
    # selected_flakes.parquet was written
    sel_p = tmp_path / "06_explorer" / "selected_flakes.parquet"
    assert sel_p.exists()
    df = pd.read_parquet(sel_p)
    assert df["flake_id"].tolist() == [100, 200, 300]


@pytest.mark.asyncio
async def test_save_state_409_when_clustering_not_committed(tmp_path: Path):
    """Pipeline raises RuntimeError → route returns 409 prerequisite_missing."""
    (tmp_path / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(tmp_path),
        "raw_images_dir": str(tmp_path / "raw"),
        "steps": {},  # No clustering / domain_proximity
    }))
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    body = {
        "include_labels": [], "exclude_labels": [],
        "neighbor_filter": {"size_min": None, "size_max": None,
                            "isolation_min": None, "exclude_border_clipped": False},
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/projects/local/run/explorer/save_state", json=body)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "prerequisite_missing"


@pytest.mark.asyncio
async def test_save_state_releases_lock_on_success(tmp_path: Path):
    """Two consecutive saves must both succeed (lock cleanly released)."""
    _seed_for_save(tmp_path)
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    body = {
        "include_labels": ["thin"], "exclude_labels": [],
        "neighbor_filter": {"size_min": None, "size_max": None,
                            "isolation_min": None, "exclude_border_clipped": False},
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r1 = await ac.post("/api/v1/projects/local/run/explorer/save_state", json=body)
        r2 = await ac.post("/api/v1/projects/local/run/explorer/save_state", json=body)
    assert r1.status_code == 200
    assert r2.status_code == 200
