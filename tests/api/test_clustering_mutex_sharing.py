"""Both clustering endpoints share the per-scan mutex (backend design §3.2).

Asserted shape: while the per-scan lock is held by an external acquisition,
a request to the clustering apply_thresholds endpoint on the SAME scan must
either return HTTP 423 ProjectBusy immediately (mutex.acquire_scan_lock
raises when already held), or be queued — we accept either, but contention
MUST be observable.

W10-C.4c rewrite: replaces the prior per-project lock semantics. We
pre-acquire `acquire_scan_lock(<sid>)` outside the route, then POST to
`/api/v1/projects/<pid>/scans/<sid>/run/clustering/apply_thresholds` and
assert the contention shape.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.logging_ctx import RequestIdMiddleware
from flake_analysis.api.mutex import acquire_scan_lock
from flake_analysis.api.routes import clustering as clustering_route
from flake_analysis.state.manifest import Manifest, save_manifest
from flake_analysis.state.paths import analysis_folder

SID = 42


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(clustering_route.router, prefix="/api/v1")
    return app


@pytest.fixture(autouse=True)
def _clear_scan_locks():
    from flake_analysis.api import mutex
    mutex._scan_locks.clear()
    yield
    mutex._scan_locks.clear()


def _seed_clustering(folder: Path) -> None:
    """Write minimal clustering artifacts so apply_thresholds reaches its work, not its prereq guard."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "04_clustering").mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "domain_id": [1, 2],
        "cluster_label": [0, 1],
        "max_posterior": [0.9, 0.8],
    }).to_parquet(folder / "04_clustering" / "assignments.parquet", index=False)
    (folder / "04_clustering" / "labels.json").write_text(json.dumps({
        "version": 1, "n_clusters": 2,
        "groups": [
            {"id": 0, "name": "a", "size": 1, "mean_rgb": [0, 0, 0]},
            {"id": 1, "name": "b", "size": 1, "mean_rgb": [0, 0, 0]},
        ],
        "assignments": {"1": 0, "2": 1},
        "thresholds": {"0": 0.5, "1": 0.5},
        "noise_label": -1, "random_state": 42, "fitted_at": "2026-05-21T00:00:00Z",
    }))
    save_manifest(Manifest(analysis_folder=str(folder)), folder)


@pytest.mark.asyncio
async def test_apply_thresholds_blocks_when_scan_lock_held(tmp_path: Path, monkeypatch):
    """Pre-acquire the per-scan lock; the clustering POST must observe contention.

    Acceptable shapes (mutex.acquire_scan_lock raises ProjectBusy synchronously
    if the lock is already held, so we expect 423 ProjectBusy in practice; a
    queued/timeout outcome would also prove the lock is shared):

    - HTTP 423 with body["error"]["code"] == "project_busy"  (preferred)
    - The request never completes within a short timeout (queued behind us)
    """
    folder = analysis_folder(tmp_path, "local", SID)
    _seed_clustering(folder)
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))

    app = _make_app()
    body = {"cluster_thresholds": {0: 0.5, 1: 0.5}}

    # Hold the per-scan lock externally for the duration of the request.
    async with acquire_scan_lock(SID):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            try:
                resp = await asyncio.wait_for(
                    ac.post(
                        f"/api/v1/projects/local/scans/{SID}/run/clustering/apply_thresholds",
                        json=body,
                    ),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                # Queued behind the lock — also a valid contention shape.
                return

    # Got a synchronous response — must be HTTP 423 ProjectBusy.
    assert resp.status_code == 423, (
        f"expected contention (423 or timeout), got {resp.status_code}: {resp.text}"
    )
    payload = resp.json()
    assert payload["error"]["code"] == "project_busy"
