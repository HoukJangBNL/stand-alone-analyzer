"""Tests for the SSE heartbeat helper (sse.sse_stream)."""
from __future__ import annotations
import asyncio
import json
import os
import pytest
from httpx import ASGITransport, AsyncClient


def test_heartbeat_constant_is_15_seconds():
    """Ops-mandated floor: 15s. Lowering requires PM signoff."""
    from flake_analysis.api.sse import SSE_HEARTBEAT_SECONDS
    assert SSE_HEARTBEAT_SECONDS == 15


@pytest.mark.asyncio
async def test_sse_stream_emits_heartbeat_when_idle():
    """When the bridge produces nothing for >interval, sse_stream yields a comment frame."""
    from flake_analysis.api.sse import ProgressBridge, sse_stream

    bridge = ProgressBridge()

    async def producer():
        # Produce nothing for 30 ms, then a single done event.
        await asyncio.sleep(0.03)
        bridge.emit_done({"ok": True})
        bridge.close()

    asyncio.create_task(producer())

    frames: list[str] = []
    # Override the interval to 10 ms so the test runs fast. The wrapper
    # accepts heartbeat_seconds for testability; production callers use the
    # default of SSE_HEARTBEAT_SECONDS.
    async for frame in sse_stream(bridge, heartbeat_seconds=0.01):
        frames.append(frame)

    heartbeat_frames = [f for f in frames if f.startswith(": ")]
    real_frames = [f for f in frames if f.startswith("event: ")]

    assert len(heartbeat_frames) >= 1, f"no heartbeat in {frames!r}"
    assert all(f == ": heartbeat\n\n" for f in heartbeat_frames)
    assert len(real_frames) == 1
    assert real_frames[0].startswith("event: done\n")


@pytest.mark.asyncio
async def test_sse_stream_no_heartbeat_when_events_flow():
    """Steady stream of events → no heartbeat needed."""
    from flake_analysis.api.sse import ProgressBridge, sse_stream

    bridge = ProgressBridge()

    async def producer():
        for i in range(5):
            bridge.emit_progress(i / 5, f"step {i}")
            await asyncio.sleep(0.001)
        bridge.emit_done({"n": 5})
        bridge.close()

    asyncio.create_task(producer())

    frames: list[str] = []
    async for frame in sse_stream(bridge, heartbeat_seconds=0.5):
        frames.append(frame)

    heartbeat_frames = [f for f in frames if f.startswith(": ")]
    assert heartbeat_frames == [], f"unexpected heartbeats: {heartbeat_frames!r}"


@pytest.mark.asyncio
async def test_sse_stream_terminates_on_close():
    """sse_stream yields the final terminal event then exits cleanly."""
    from flake_analysis.api.sse import ProgressBridge, sse_stream

    bridge = ProgressBridge()
    bridge.emit_done({"ok": True})
    bridge.close()

    frames = [frame async for frame in sse_stream(bridge, heartbeat_seconds=10)]
    assert any(f.startswith("event: done\n") for f in frames)


@pytest.mark.asyncio
async def test_run_thumbnails_emits_heartbeat_when_pipeline_is_slow(tmp_path, monkeypatch):
    """Slow pipeline → at least one heartbeat frame leaks onto the wire."""
    from unittest.mock import patch
    from flake_analysis.api.main import create_app
    from flake_analysis.state.manifest import Manifest, save_manifest

    analysis_folder = tmp_path / "proj"
    analysis_folder.mkdir()
    raw_images_dir = tmp_path / "raw"
    raw_images_dir.mkdir()
    save_manifest(
        Manifest(analysis_folder=str(analysis_folder), raw_images_dir=str(raw_images_dir)),
        analysis_folder,
    )
    monkeypatch.setenv("SAA_ANALYSIS_FOLDER", str(analysis_folder))

    # Force the heartbeat to fire fast so the test stays under a second.
    monkeypatch.setattr("flake_analysis.api.sse.SSE_HEARTBEAT_SECONDS", 0.05)

    def slow_pipeline(**kwargs):
        # Sleep without emitting any progress so the bridge stays idle and
        # sse_stream is forced into its heartbeat branch.
        import time
        time.sleep(0.2)
        return {
            "output_dir": str(analysis_folder / "00_thumbnails"),
            "n_images": 0,
            "n_skipped": 0,
            "n_failed": 0,
            "params": {},
            "params_hash": "sha256:x",
            "cache_dir": None,
        }

    with patch("flake_analysis.api.routes.run.run_thumbnails_step", side_effect=slow_pipeline):
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/projects/local/run/thumbnails", json={"quality": 80}
            )
            assert resp.status_code == 200
            body = resp.text

    assert ": heartbeat\n\n" in body, f"no heartbeat seen in body: {body[:500]!r}"
    assert "event: done\n" in body
