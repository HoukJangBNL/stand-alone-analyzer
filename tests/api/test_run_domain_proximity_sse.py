# tests/api/test_run_domain_proximity_sse.py
import pytest
import asyncio
import json
import os
import threading
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport
from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest, save_manifest


def _setup_project(tmp_path):
    """Create a manifest on disk with annotations_path.

    Domain proximity is independent of Background / Domain Stats — it only
    requires annotations.json. The pipeline function is mocked at the route
    boundary so we don't need a real annotations payload either.
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
        steps={},
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
async def test_run_domain_proximity_sse(tmp_path, _clean_env):
    """POST /run/domain_proximity streams progress and completes."""
    analysis_folder = _setup_project(tmp_path)

    def mock_run_domain_proximity(**kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            cb(0.0, "start")
            cb(0.5, "halfway")
            cb(1.0, "done")
        return {
            "distances_path": str(analysis_folder / "05_domain_proximity" / "distances.parquet"),
            "flake_assignments_path": str(
                analysis_folder / "05_domain_proximity" / "flake_assignments.parquet"
            ),
            "n_pairs": 12,
            "n_domains": 5,
            "n_flakes": 3,
            "params": {
                "r_max_px": 200.0,
                "min_area_px": 10,
                "max_area_px": None,
                "d_touch_px": 2.0,
                "pixel_size_um": 0.5,
                "link_distance_um": 5.0,
                "workers": 4,
            },
        }

    with patch(
        "flake_analysis.api.routes.run.run_domain_proximity_step",
        side_effect=mock_run_domain_proximity,
    ):
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/api/v1/projects/p_dp/run/domain_proximity",
                json={
                    "r_max_px": 200.0,
                    "min_area_px": 10,
                    "d_touch_px": 2.0,
                    "pixel_size_um": 0.5,
                    "link_distance_um": 5.0,
                    "workers": 4,
                },
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
                assert done_events[0]["result"]["n_flakes"] == 3
                assert done_events[0]["result"]["n_pairs"] == 12
                assert done_events[0]["result"]["n_domains"] == 5


@pytest.mark.asyncio
async def test_run_domain_proximity_sse_wire_format(tmp_path, _clean_env):
    """Raw response body contains 'event: progress' and 'event: done' framing."""
    _setup_project(tmp_path)

    def mock_run_domain_proximity(**kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            cb(0.0, "start")
            cb(1.0, "done")
        return {
            "distances_path": "/tmp/distances.parquet",
            "flake_assignments_path": "/tmp/flake_assignments.parquet",
            "n_pairs": 1,
            "n_domains": 1,
            "n_flakes": 1,
            "params": {
                "r_max_px": 200.0,
                "min_area_px": 10,
                "max_area_px": None,
                "d_touch_px": 2.0,
                "pixel_size_um": 0.5,
                "link_distance_um": 5.0,
                "workers": 4,
            },
        }

    with patch(
        "flake_analysis.api.routes.run.run_domain_proximity_step",
        side_effect=mock_run_domain_proximity,
    ):
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/projects/p_dp_wire/run/domain_proximity",
                json={},
            )
            assert resp.status_code == 200
            body = resp.text

    assert "event: progress\ndata: " in body
    assert "event: done\ndata: " in body
    # SSE frame separator: blank line after each event.
    assert "\n\n" in body


@pytest.mark.asyncio
async def test_run_domain_proximity_mutex_contention_returns_locked(tmp_path, _clean_env):
    """Concurrent requests to same project: second gets HTTP 423, NOT a stream error."""
    _setup_project(tmp_path)

    release = threading.Event()
    started = threading.Event()

    def mock_run_domain_proximity(**kwargs):
        # Block the worker thread until the test releases it. This keeps the
        # project lock held and lets a second request collide.
        started.set()
        release.wait(timeout=5.0)
        cb = kwargs.get("progress_callback")
        if cb:
            cb(1.0, "done")
        return {
            "distances_path": "/tmp/distances.parquet",
            "flake_assignments_path": "/tmp/flake_assignments.parquet",
            "n_pairs": 0,
            "n_domains": 0,
            "n_flakes": 0,
            "params": {
                "r_max_px": 200.0,
                "min_area_px": 10,
                "max_area_px": None,
                "d_touch_px": 2.0,
                "pixel_size_um": 0.5,
                "link_distance_um": 5.0,
                "workers": 4,
            },
        }

    with patch(
        "flake_analysis.api.routes.run.run_domain_proximity_step",
        side_effect=mock_run_domain_proximity,
    ):
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async def first_request():
                async with client.stream(
                    "POST", "/api/v1/projects/p_dp_busy/run/domain_proximity", json={}
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
                    "/api/v1/projects/p_dp_busy/run/domain_proximity", json={}
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
async def test_run_domain_proximity_pipeline_error_emits_sse_error_event(tmp_path, _clean_env):
    """A pipeline exception is delivered as an SSE 'error' event with the REST envelope shape."""
    _setup_project(tmp_path)

    def mock_run_domain_proximity(**kwargs):
        raise RuntimeError("annotations missing required fields")

    with patch(
        "flake_analysis.api.routes.run.run_domain_proximity_step",
        side_effect=mock_run_domain_proximity,
    ):
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream(
                "POST", "/api/v1/projects/p_dp_err/run/domain_proximity", json={}
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
    assert "annotations missing required fields" in err["error"]["message"]
    assert err["error"]["details"]["exc_type"] == "RuntimeError"
    assert "request_id" in err["error"]
