"""SSE refit route — error path + lock-release semantics.

The happy path is exercised end-to-end via tests/api/test_clustering_mutex_sharing.py;
this file focuses on the contract surface (route exists, params validate, lock releases).
"""
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.logging_ctx import RequestIdMiddleware
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


def _setup(tmp_path: Path, monkeypatch, pid: str = "local", sid: int = SID) -> Path:
    folder = analysis_folder(tmp_path, pid, sid)
    folder.mkdir(parents=True)
    save_manifest(Manifest(analysis_folder=str(folder)), folder)
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    return folder


@pytest.mark.asyncio
async def test_refit_streams_error_when_prereq_missing(tmp_path: Path, monkeypatch):
    """No domain_stats / selector commit → wrapper raises RuntimeError → SSE 'error' event."""
    _setup(tmp_path, monkeypatch)
    app = _make_app()

    body = {
        "seed_groups": [
            {"name": "a", "domain_ids": [1, 2]},
            {"name": "b", "domain_ids": [3, 4]},
        ],
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream(
            "POST",
            f"/api/v1/projects/local/scans/{SID}/run/clustering/refit",
            json=body,
        ) as r:
            assert r.status_code == 200  # SSE convention
            text = ""
            async for chunk in r.aiter_text():
                text += chunk
            assert 'event: error' in text or '"type": "error"' in text or '"type":"error"' in text


@pytest.mark.asyncio
async def test_refit_releases_lock_after_error(tmp_path: Path, monkeypatch):
    """After the first request errors out, the scan mutex must be free for the next request."""
    _setup(tmp_path, monkeypatch)
    app = _make_app()

    body = {"seed_groups": [{"name": "a", "domain_ids": [1, 2]}, {"name": "b", "domain_ids": [3]}]}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # First call: drains
        async with ac.stream(
            "POST",
            f"/api/v1/projects/local/scans/{SID}/run/clustering/refit",
            json=body,
        ) as r1:
            async for _ in r1.aiter_text():
                pass
        # Second call: must NOT 423 (lock should be released)
        async with ac.stream(
            "POST",
            f"/api/v1/projects/local/scans/{SID}/run/clustering/refit",
            json=body,
        ) as r2:
            assert r2.status_code != 423
            async for _ in r2.aiter_text():
                pass
