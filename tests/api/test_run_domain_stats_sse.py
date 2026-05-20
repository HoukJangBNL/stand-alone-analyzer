# tests/api/test_run_domain_stats_sse.py
import pytest
import asyncio
import json
import os
import threading
from datetime import datetime, timezone
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport
from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest, StepEntry, save_manifest


def _setup_project(tmp_path):
    """Create a manifest on disk with annotations_path + completed background step.

    Domain stats requires the upstream Background step to have run, so the
    manifest must already record a 'background' StepEntry. The pipeline
    function is mocked at the route boundary so we don't actually need the
    background.npy file on disk for these tests.
    """
    analysis_folder = tmp_path / "proj"
    analysis_folder.mkdir()
    raw_images_dir = tmp_path / "raw"
    raw_images_dir.mkdir()
    annotations_path = tmp_path / "annotations.json"
    annotations_path.write_text("{}", encoding="utf-8")

    m = Manifest(
        analysis_folder=str(analysis_folder),
        raw_images_dir=str(raw_images_dir),
        annotations_path=str(annotations_path),
        steps={
            "background": StepEntry(
                completed_at=datetime.now(timezone.utc).isoformat(),
                params={"seed": 0, "max_images": 1, "gaussian_sigma": 10.0, "method": "median"},
                params_hash="bg_hash_stub",
                outputs={"background_npy": "01_background/background.npy"},
            ),
        },
    )
    save_manifest(m, analysis_folder)
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis_folder)
    return analysis_folder


@pytest.fixture(autouse=True)
def _clear_project_locks():
    """Per-project locks are module-global; clear between tests to avoid leaks."""
    from flake_analysis.api import mutex
    mutex._project_locks.clear()
    yield
    mutex._project_locks.clear()


@pytest.fixture
def _clean_env():
    yield
    os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_run_domain_stats_sse(tmp_path, _clean_env):
    """POST /run/domain_stats streams progress and completes."""
    analysis_folder = _setup_project(tmp_path)

    def mock_run_domain_stats(**kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            cb(0.0, "start")
            cb(0.5, "halfway")
            cb(1.0, "done")
        return {
            "output_path": str(analysis_folder / "02_domain_stats" / "stats.npz"),
            "num_flakes": 42,
            "params": {"repr_mode": "median", "raw_ext": ".png"},
        }

    with patch("flake_analysis.api.routes.run.run_domain_stats_step", side_effect=mock_run_domain_stats):
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/api/v1/projects/p_ds/run/domain_stats",
                json={"repr_mode": "median", "raw_ext": ".png"},
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
                assert done_events[0]["result"]["num_flakes"] == 42


@pytest.mark.asyncio
async def test_run_domain_stats_sse_wire_format(tmp_path, _clean_env):
    """Raw response body contains 'event: progress' and 'event: done' framing."""
    _setup_project(tmp_path)

    def mock_run_domain_stats(**kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            cb(0.0, "start")
            cb(1.0, "done")
        return {
            "output_path": "/tmp/stats.npz",
            "num_flakes": 7,
            "params": {"repr_mode": "median", "raw_ext": ".png"},
        }

    with patch("flake_analysis.api.routes.run.run_domain_stats_step", side_effect=mock_run_domain_stats):
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/projects/p_ds_wire/run/domain_stats",
                json={"repr_mode": "median"},
            )
            assert resp.status_code == 200
            body = resp.text

    assert "event: progress\ndata: " in body
    assert "event: done\ndata: " in body
    # SSE frame separator: blank line after each event.
    assert "\n\n" in body


@pytest.mark.asyncio
async def test_run_domain_stats_mutex_contention_returns_locked(tmp_path, _clean_env):
    """Concurrent requests to same project: second gets HTTP 423, NOT a stream error."""
    _setup_project(tmp_path)

    release = threading.Event()
    started = threading.Event()

    def mock_run_domain_stats(**kwargs):
        # Block the worker thread until the test releases it. This keeps the
        # project lock held and lets a second request collide.
        started.set()
        release.wait(timeout=5.0)
        cb = kwargs.get("progress_callback")
        if cb:
            cb(1.0, "done")
        return {
            "output_path": "/tmp/stats.npz",
            "num_flakes": 1,
            "params": {"repr_mode": "median", "raw_ext": ".png"},
        }

    with patch("flake_analysis.api.routes.run.run_domain_stats_step", side_effect=mock_run_domain_stats):
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async def first_request():
                async with client.stream(
                    "POST", "/api/v1/projects/p_ds_busy/run/domain_stats", json={"repr_mode": "median"}
                ) as resp:
                    assert resp.status_code == 200
                    # Wait until the worker is blocked before letting the
                    # second request fire.
                    while not started.is_set():
                        await asyncio.sleep(0.01)
                    # Hand off to the scheduler so the second request runs.
                    await asyncio.sleep(0.05)
                    release.set()
                    # Drain the stream so the connection closes cleanly.
                    async for _ in resp.aiter_lines():
                        pass
                    return resp.status_code

            async def second_request():
                # Wait until the first request has the lock.
                while not started.is_set():
                    await asyncio.sleep(0.01)
                resp = await client.post(
                    "/api/v1/projects/p_ds_busy/run/domain_stats", json={"repr_mode": "median"}
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
async def test_run_domain_stats_pipeline_error_emits_sse_error_event(tmp_path, _clean_env):
    """A pipeline exception is delivered as an SSE 'error' event with the REST envelope shape."""
    _setup_project(tmp_path)

    def mock_run_domain_stats(**kwargs):
        raise RuntimeError("background not ready")

    with patch("flake_analysis.api.routes.run.run_domain_stats_step", side_effect=mock_run_domain_stats):
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream(
                "POST", "/api/v1/projects/p_ds_err/run/domain_stats", json={"repr_mode": "median"}
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
    assert "background not ready" in err["error"]["message"]
    assert err["error"]["details"]["exc_type"] == "RuntimeError"
    assert "request_id" in err["error"]
