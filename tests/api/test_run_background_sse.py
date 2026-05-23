# tests/api/test_run_background_sse.py
import pytest
import asyncio
import json
import threading
from unittest.mock import patch
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.logging_ctx import RequestIdMiddleware
from flake_analysis.api.routes import run as run_route
from flake_analysis.state.manifest import Manifest, save_manifest
from flake_analysis.state.paths import analysis_folder

# Test scan id used by tests that don't carry a real DB row.
SID = 42


def _make_app() -> FastAPI:
    """Mini-app exposing only the run router (W10-C.4b)."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(run_route.router, prefix="/api/v1")
    return app


def _setup_project(tmp_path, monkeypatch, pid: str = "p_bg", sid: int = SID):
    """Create a per-scan manifest on disk and point SAA_ANALYSIS_ROOT at tmp_path."""
    folder = analysis_folder(tmp_path, pid, sid)
    folder.mkdir(parents=True)
    raw_images_dir = tmp_path / "raw"
    raw_images_dir.mkdir()

    m = Manifest(
        analysis_folder=str(folder),
        raw_images_dir=str(raw_images_dir),
    )
    save_manifest(m, folder)
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    return folder


@pytest.fixture(autouse=True)
def _clear_scan_locks():
    """Per-scan locks are module-global; clear between tests to avoid leaks."""
    from flake_analysis.api import mutex
    mutex._scan_locks.clear()
    yield
    mutex._scan_locks.clear()


@pytest.fixture
def _client_app():
    return _make_app()


def _stub_session_dep(app):
    """Override get_db_session with a no-op session for usage event emission."""
    from unittest.mock import AsyncMock, MagicMock

    from flake_analysis.api.deps import get_db_session

    mock_session = MagicMock()
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.commit = AsyncMock()

    async def _yield():
        yield mock_session

    app.dependency_overrides[get_db_session] = _yield


@pytest.mark.asyncio
async def test_run_background_sse(tmp_path, monkeypatch, _client_app):
    """POST /run/background streams progress and completes."""
    folder = _setup_project(tmp_path, monkeypatch, pid="p_bg", sid=SID)

    def mock_run_background(**kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            cb(0.0, "start")
            cb(0.5, "halfway")
            cb(1.0, "done")
        return {
            "output_path": str(folder / "01_background" / "background.npy"),
            "shape": (1024, 1024, 3),
            "params": {
                "seed": 0,
                "max_images": 100,
                "gaussian_sigma": 10.0,
                "method": "median",
            },
        }

    _stub_session_dep(_client_app)
    with patch("flake_analysis.api.routes.run.run_background_step", side_effect=mock_run_background):
        async with AsyncClient(transport=ASGITransport(app=_client_app), base_url="http://test") as client:
            async with client.stream(
                "POST",
                f"/api/v1/projects/p_bg/scans/{SID}/run/background",
                json={"seed": 0, "max_images": 100, "gaussian_sigma": 10.0, "method": "median"},
            ) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers["content-type"]

                events = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        events.append(data)

                progress_events = [e for e in events if e["type"] == "progress"]
                done_events = [e for e in events if e["type"] == "done"]

                assert len(progress_events) == 3
                assert len(done_events) == 1
                assert done_events[0]["result"]["shape"] == [1024, 1024, 3]


@pytest.mark.asyncio
async def test_run_background_sse_wire_format(tmp_path, monkeypatch, _client_app):
    """Raw response body contains 'event: progress' and 'event: done' framing."""
    _setup_project(tmp_path, monkeypatch, pid="p_bg_wire", sid=SID)

    def mock_run_background(**kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            cb(0.0, "start")
            cb(1.0, "done")
        return {
            "output_path": "/tmp/bg.npy",
            "shape": (512, 512, 3),
            "params": {"seed": 0, "max_images": 1, "gaussian_sigma": 10.0, "method": "median"},
        }

    _stub_session_dep(_client_app)
    with patch("flake_analysis.api.routes.run.run_background_step", side_effect=mock_run_background):
        async with AsyncClient(transport=ASGITransport(app=_client_app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/v1/projects/p_bg_wire/scans/{SID}/run/background",
                json={"seed": 0, "max_images": 1},
            )
            assert resp.status_code == 200
            body = resp.text

    assert "event: progress\ndata: " in body
    assert "event: done\ndata: " in body
    # SSE frame separator: blank line after each event.
    assert "\n\n" in body


@pytest.mark.asyncio
async def test_run_background_mutex_contention_returns_locked(tmp_path, monkeypatch, _client_app):
    """Concurrent requests to same scan: second gets HTTP 423, NOT a stream error."""
    _setup_project(tmp_path, monkeypatch, pid="p_bg_busy", sid=SID)

    release = threading.Event()
    started = threading.Event()

    def mock_run_background(**kwargs):
        # Block the worker thread until the test releases it. This keeps the
        # scan lock held and lets a second request collide.
        started.set()
        release.wait(timeout=5.0)
        cb = kwargs.get("progress_callback")
        if cb:
            cb(1.0, "done")
        return {
            "output_path": "/tmp/bg.npy",
            "shape": (256, 256, 3),
            "params": {"seed": 0, "max_images": 1, "gaussian_sigma": 10.0, "method": "median"},
        }

    _stub_session_dep(_client_app)
    with patch("flake_analysis.api.routes.run.run_background_step", side_effect=mock_run_background):
        async with AsyncClient(transport=ASGITransport(app=_client_app), base_url="http://test") as client:
            async def first_request():
                async with client.stream(
                    "POST", f"/api/v1/projects/p_bg_busy/scans/{SID}/run/background", json={"seed": 0}
                ) as resp:
                    assert resp.status_code == 200
                    while not started.is_set():
                        await asyncio.sleep(0.01)
                    await asyncio.sleep(0.05)
                    release.set()
                    async for _ in resp.aiter_lines():
                        pass
                    return resp.status_code

            async def second_request():
                while not started.is_set():
                    await asyncio.sleep(0.01)
                resp = await client.post(
                    f"/api/v1/projects/p_bg_busy/scans/{SID}/run/background", json={"seed": 0}
                )
                return resp

            first_task = asyncio.create_task(first_request())
            second_resp = await second_request()
            await first_task

    # ProjectBusy.status_code is HTTP_423_LOCKED. Critical: this is a regular
    # HTTP error, NOT a streaming response that opens with 200 and errors.
    assert second_resp.status_code == 423
    assert "text/event-stream" not in second_resp.headers.get("content-type", "")
    body = second_resp.json()
    assert body["error"]["code"] == "project_busy"
    assert "request_id" in body["error"]


@pytest.mark.asyncio
async def test_run_background_pipeline_error_emits_sse_error_event(tmp_path, monkeypatch, _client_app):
    """A pipeline exception is delivered as an SSE 'error' event with the REST envelope shape."""
    _setup_project(tmp_path, monkeypatch, pid="p_bg_err", sid=SID)

    def mock_run_background(**kwargs):
        raise RuntimeError("no raw images")

    _stub_session_dep(_client_app)
    with patch("flake_analysis.api.routes.run.run_background_step", side_effect=mock_run_background):
        async with AsyncClient(transport=ASGITransport(app=_client_app), base_url="http://test") as client:
            async with client.stream(
                "POST", f"/api/v1/projects/p_bg_err/scans/{SID}/run/background", json={"seed": 0}
            ) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers["content-type"]

                events = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))

    error_events = [e for e in events if e["type"] == "error"]
    assert len(error_events) == 1
    err = error_events[0]
    assert err["error"]["code"] == "pipeline_failed"
    assert "no raw images" in err["error"]["message"]
    assert err["error"]["details"]["exc_type"] == "RuntimeError"
    assert "request_id" in err["error"]
