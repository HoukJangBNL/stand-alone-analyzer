import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.logging_ctx import RequestIdMiddleware
from flake_analysis.api.routes import selector as selector_route
from flake_analysis.state.manifest import Manifest, save_manifest
from flake_analysis.state.paths import analysis_folder

SID = 42


def _make_app() -> FastAPI:
    """Mini-app exposing only the selector router (W10-C.4b)."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(selector_route.router, prefix="/api/v1")
    return app


@pytest.fixture(autouse=True)
def _clear_scan_locks():
    from flake_analysis.api import mutex
    mutex._scan_locks.clear()
    yield
    mutex._scan_locks.clear()


@pytest.mark.asyncio
async def test_run_selector_sse_streams_progress_and_done(tmp_path, monkeypatch):
    pid, sid = "local", SID
    folder = analysis_folder(tmp_path, pid, sid)
    folder.mkdir(parents=True)
    raw = tmp_path / "raw"
    raw.mkdir()
    save_manifest(
        Manifest(analysis_folder=str(folder), raw_images_dir=str(raw)),
        folder,
    )
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))

    def mock_run_selector(**kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            cb(0.0, "loading")
            cb(0.5, "filtering")
            cb(1.0, "done")
        return {
            "output_path": str(folder / "03_selector" / "selection.parquet"),
            "selected_count": 7,
            "total_count": 12,
            "params": {"area_min": 5.0},
            "params_hash": "sha256:zzz",
        }

    with patch(
        "flake_analysis.api.routes.selector.run_selector_step",
        side_effect=mock_run_selector,
    ):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream(
                "POST",
                f"/api/v1/projects/{pid}/scans/{sid}/run/selector",
                json={"area_min": 5.0},
            ) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers["content-type"]
                events = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))
                progress = [e for e in events if e["type"] == "progress"]
                done = [e for e in events if e["type"] == "done"]
                assert len(progress) == 3
                assert len(done) == 1
                assert done[0]["result"]["selected_count"] == 7


@pytest.mark.asyncio
async def test_run_selector_propagates_pipeline_error(tmp_path, monkeypatch):
    pid, sid = "local", SID
    folder = analysis_folder(tmp_path, pid, sid)
    folder.mkdir(parents=True)
    raw = tmp_path / "raw"
    raw.mkdir()
    save_manifest(
        Manifest(analysis_folder=str(folder), raw_images_dir=str(raw)),
        folder,
    )
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))

    def boom(**_kwargs):
        raise RuntimeError("Domain Stats step not completed.")

    with patch(
        "flake_analysis.api.routes.selector.run_selector_step",
        side_effect=boom,
    ):
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream(
                "POST",
                f"/api/v1/projects/{pid}/scans/{sid}/run/selector",
                json={},
            ) as resp:
                events = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))
                err = [e for e in events if e["type"] == "error"]
                assert len(err) == 1
                assert err[0]["error"]["code"] == "pipeline_failed"
                assert "Domain Stats" in err[0]["error"]["message"]
