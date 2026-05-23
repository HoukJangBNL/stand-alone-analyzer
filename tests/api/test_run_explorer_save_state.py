"""save_state is SYNCHRONOUS JSON (pinned decision #12) — NOT SSE."""
import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.logging_ctx import RequestIdMiddleware
from flake_analysis.api.routes import explorer as explorer_route
from flake_analysis.state.paths import analysis_folder

SID = 42


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(explorer_route.router, prefix="/api/v1")
    return app


@pytest.fixture(autouse=True)
def _clear_scan_locks():
    from flake_analysis.api import mutex
    mutex._scan_locks.clear()
    yield
    mutex._scan_locks.clear()


def _seed_for_save(folder: Path) -> None:
    """Need clustering+proximity steps committed for save_explorer_state to succeed."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "04_clustering").mkdir(parents=True, exist_ok=True)
    (folder / "05_domain_proximity").mkdir(parents=True, exist_ok=True)
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
async def test_save_state_returns_json_200_not_sse(tmp_path: Path, monkeypatch):
    folder = analysis_folder(tmp_path, "local", SID)
    _seed_for_save(folder)
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    app = _make_app()

    body = {
        "include_labels": ["thin"],
        "exclude_labels": [],
        "neighbor_filter": {"size_min": 1, "size_max": 50,
                            "isolation_min": None, "exclude_border_clipped": False},
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/projects/local/scans/{SID}/run/explorer/save_state",
            json=body,
        )

    assert resp.status_code == 200
    # Must NOT be SSE — content-type stays application/json
    assert resp.headers.get("content-type", "").startswith("application/json")
    payload = resp.json()
    assert "state_path" in payload
    assert payload["state_path"].endswith("explorer_state.json")
    assert payload["selected_count"] is None  # no selected_flake_ids in body


@pytest.mark.asyncio
async def test_save_state_persists_selected_flake_ids(tmp_path: Path, monkeypatch):
    folder = analysis_folder(tmp_path, "local", SID)
    _seed_for_save(folder)
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    app = _make_app()

    body = {
        "include_labels": [],
        "exclude_labels": [],
        "neighbor_filter": {"size_min": None, "size_max": None,
                            "isolation_min": None, "exclude_border_clipped": False},
        "selected_flake_ids": [100, 200, 300],
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/projects/local/scans/{SID}/run/explorer/save_state",
            json=body,
        )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["selected_count"] == 3
    # selected_flakes.parquet was written
    sel_p = folder / "06_explorer" / "selected_flakes.parquet"
    assert sel_p.exists()
    df = pd.read_parquet(sel_p)
    assert df["flake_id"].tolist() == [100, 200, 300]


@pytest.mark.asyncio
async def test_save_state_409_when_clustering_not_committed(tmp_path: Path, monkeypatch):
    """Pipeline raises RuntimeError → route returns 409 prerequisite_missing."""
    folder = analysis_folder(tmp_path, "local", SID)
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(folder / "raw"),
        "steps": {},  # No clustering / domain_proximity
    }))
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    app = _make_app()

    body = {
        "include_labels": [], "exclude_labels": [],
        "neighbor_filter": {"size_min": None, "size_max": None,
                            "isolation_min": None, "exclude_border_clipped": False},
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/v1/projects/local/scans/{SID}/run/explorer/save_state",
            json=body,
        )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "prerequisite_missing"


@pytest.mark.asyncio
async def test_save_state_releases_lock_on_success(tmp_path: Path, monkeypatch):
    """Two consecutive saves must both succeed (lock cleanly released)."""
    folder = analysis_folder(tmp_path, "local", SID)
    _seed_for_save(folder)
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    app = _make_app()

    body = {
        "include_labels": ["thin"], "exclude_labels": [],
        "neighbor_filter": {"size_min": None, "size_max": None,
                            "isolation_min": None, "exclude_border_clipped": False},
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r1 = await ac.post(
            f"/api/v1/projects/local/scans/{SID}/run/explorer/save_state",
            json=body,
        )
        r2 = await ac.post(
            f"/api/v1/projects/local/scans/{SID}/run/explorer/save_state",
            json=body,
        )
    assert r1.status_code == 200
    assert r2.status_code == 200
