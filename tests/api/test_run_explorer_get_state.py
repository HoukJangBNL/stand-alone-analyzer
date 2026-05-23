import json
from pathlib import Path

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


def _seed_with_saved_state(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "04_clustering").mkdir(parents=True, exist_ok=True)
    (folder / "05_domain_proximity").mkdir(parents=True, exist_ok=True)
    (folder / "06_explorer").mkdir(parents=True, exist_ok=True)
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
async def test_get_state_returns_saved_payload(tmp_path: Path, monkeypatch):
    folder = analysis_folder(tmp_path, "local", SID)
    _seed_with_saved_state(folder)
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))

    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            f"/api/v1/projects/local/scans/{SID}/run/explorer/state"
        )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["include_labels"] == ["thin"]
    assert payload["neighbor_filter"]["size_min"] == 1


@pytest.mark.asyncio
async def test_get_state_404_when_unsaved(tmp_path: Path, monkeypatch):
    folder = analysis_folder(tmp_path, "local", SID)
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({
        "version": 1, "analysis_folder": str(folder),
        "raw_images_dir": str(folder / "raw"),
        "steps": {},
    }))
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))

    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            f"/api/v1/projects/local/scans/{SID}/run/explorer/state"
        )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "explorer_state_missing"
