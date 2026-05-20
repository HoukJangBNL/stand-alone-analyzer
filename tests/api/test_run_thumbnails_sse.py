# tests/api/test_run_thumbnails_sse.py
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
    """Create a manifest on disk and point SAA_ANALYSIS_FOLDER at it."""
    analysis_folder = tmp_path / "proj"
    analysis_folder.mkdir()
    raw_images_dir = tmp_path / "raw"
    raw_images_dir.mkdir()

    m = Manifest(
        analysis_folder=str(analysis_folder),
        raw_images_dir=str(raw_images_dir),
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
async def test_run_thumbnails_sse(tmp_path, _clean_env):
    """POST /run/thumbnails streams progress and completes."""
    analysis_folder = _setup_project(tmp_path)

    def mock_run_thumbnails(**kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            cb(0.0, "start")
            cb(0.5, "halfway")
            cb(1.0, "done")
        return {
            "output_dir": str(analysis_folder / "00_thumbnails"),
            "n_images": 10,
            "n_skipped": 0,
            "n_failed": 0,
            "params": {"quality": 80},
            "params_hash": "sha256:abc",
            "cache_dir": None,
        }

    with patch("flake_analysis.api.routes.run.run_thumbnails_step", side_effect=mock_run_thumbnails):
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream("POST", "/api/v1/projects/local/run/thumbnails", json={"quality": 80}) as resp:
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
                assert done_events[0]["result"]["n_images"] == 10


@pytest.mark.asyncio
async def test_run_thumbnails_progress_drains_concurrently(tmp_path, _clean_env):
    """With >128 progress events the consumer drains concurrently with the producer.

    Regression guard for blocker #1: the prior implementation awaited the
    pipeline to completion before iterating the bridge stream, so the queue
    (maxsize=128) saturated and any events past the 128th were silently
    dropped. With concurrent drain, we should observe substantially more
    than 128 events delivered. We can't guarantee zero drops because the
    producer thread can still out-pace the asyncio consumer, but we MUST
    see strictly more than the queue capacity.
    """
    _setup_project(tmp_path)

    n_events = 300  # well above ProgressBridge queue maxsize=128

    def mock_run_thumbnails(**kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            for i in range(n_events):
                cb(i / n_events, f"step {i}")
        return {
            "output_dir": "/tmp/x",
            "n_images": n_events,
            "n_skipped": 0,
            "n_failed": 0,
            "params": {},
            "params_hash": "sha256:x",
            "cache_dir": None,
        }

    with patch("flake_analysis.api.routes.run.run_thumbnails_step", side_effect=mock_run_thumbnails):
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream(
                "POST", "/api/v1/projects/p_drain/run/thumbnails", json={"quality": 80}
            ) as resp:
                assert resp.status_code == 200

                events = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))

    progress_events = [e for e in events if e["type"] == "progress"]
    done_events = [e for e in events if e["type"] == "done"]

    # Queue maxsize is 128. Without concurrent drain, the queue saturates
    # and we'd observe at most 128 progress events. Any value strictly
    # greater than 128 proves the consumer is draining concurrently.
    assert len(progress_events) > 128, (
        f"consumer not draining concurrently: only {len(progress_events)} of {n_events} "
        f"progress events delivered (queue cap is 128)"
    )
    assert len(done_events) == 1


@pytest.mark.asyncio
async def test_run_thumbnails_sse_wire_format(tmp_path, _clean_env):
    """Raw response body contains 'event: progress' and 'event: done' framing."""
    _setup_project(tmp_path)

    def mock_run_thumbnails(**kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            cb(0.0, "start")
            cb(1.0, "done")
        return {
            "output_dir": "/tmp/x",
            "n_images": 1,
            "n_skipped": 0,
            "n_failed": 0,
            "params": {},
            "params_hash": "sha256:x",
            "cache_dir": None,
        }

    with patch("flake_analysis.api.routes.run.run_thumbnails_step", side_effect=mock_run_thumbnails):
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/v1/projects/p_wire/run/thumbnails", json={"quality": 80})
            assert resp.status_code == 200
            body = resp.text

    assert "event: progress\ndata: " in body
    assert "event: done\ndata: " in body
    # SSE frame separator: blank line after each event.
    assert "\n\n" in body


@pytest.mark.asyncio
async def test_run_thumbnails_mutex_contention_returns_locked(tmp_path, _clean_env):
    """Concurrent requests to same project: second gets HTTP 423, NOT a stream error."""
    _setup_project(tmp_path)

    release = threading.Event()
    started = threading.Event()

    def mock_run_thumbnails(**kwargs):
        # Block the worker thread until the test releases it. This keeps the
        # project lock held and lets a second request collide.
        started.set()
        release.wait(timeout=5.0)
        cb = kwargs.get("progress_callback")
        if cb:
            cb(1.0, "done")
        return {
            "output_dir": "/tmp/x",
            "n_images": 1,
            "n_skipped": 0,
            "n_failed": 0,
            "params": {},
            "params_hash": "sha256:x",
            "cache_dir": None,
        }

    with patch("flake_analysis.api.routes.run.run_thumbnails_step", side_effect=mock_run_thumbnails):
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async def first_request():
                async with client.stream(
                    "POST", "/api/v1/projects/p_busy/run/thumbnails", json={"quality": 80}
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
                    "/api/v1/projects/p_busy/run/thumbnails", json={"quality": 80}
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
async def test_run_thumbnails_pipeline_error_emits_sse_error_event(tmp_path, _clean_env):
    """A pipeline exception is delivered as an SSE 'error' event with the REST envelope shape."""
    _setup_project(tmp_path)

    def mock_run_thumbnails(**kwargs):
        raise RuntimeError("disk full")

    with patch("flake_analysis.api.routes.run.run_thumbnails_step", side_effect=mock_run_thumbnails):
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream(
                "POST", "/api/v1/projects/p_err/run/thumbnails", json={"quality": 80}
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
    assert "disk full" in err["error"]["message"]
    assert err["error"]["details"]["exc_type"] == "RuntimeError"
    assert "request_id" in err["error"]
