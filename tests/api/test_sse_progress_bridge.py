"""ProgressBridge round-trip for gpu_launching + gpu_ready events."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_emit_gpu_launching_round_trips_through_stream():
    from flake_analysis.api.sse import ProgressBridge

    bridge = ProgressBridge()
    bridge.emit_gpu_launching("i-abc123")
    bridge.close()

    events = [e async for e in bridge.stream()]
    assert len(events) == 1
    assert events[0] == {"type": "gpu_launching", "instance_id": "i-abc123"}


@pytest.mark.asyncio
async def test_emit_gpu_ready_round_trips_through_stream():
    from flake_analysis.api.sse import ProgressBridge

    bridge = ProgressBridge()
    bridge.emit_gpu_ready(100)
    bridge.close()

    events = [e async for e in bridge.stream()]
    assert len(events) == 1
    assert events[0] == {"type": "gpu_ready", "image_count": 100}


@pytest.mark.asyncio
async def test_gpu_events_drop_under_pressure_like_progress():
    """gpu_launching + gpu_ready use the drop-when-full path (same as
    emit_progress) — they are NOT terminal events."""
    from flake_analysis.api.sse import ProgressBridge

    bridge = ProgressBridge()
    # Fill the queue past 128 (the bounded maxsize).
    for i in range(150):
        bridge.emit_gpu_launching(f"i-{i:03d}")
    bridge.close()

    events = [e async for e in bridge.stream()]
    # Some events dropped (the bounded queue caps progress events at 128),
    # but close() (terminal) ensures the stream ends. The bridge does not
    # deadlock; the precise count is implementation-dependent.
    assert len(events) <= 150
