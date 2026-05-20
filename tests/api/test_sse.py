# tests/api/test_sse.py
import pytest
import asyncio
from flake_analysis.api.sse import emit_sse_event, ProgressBridge

def test_emit_sse_event():
    """emit_sse_event formats SSE lines correctly."""
    result = emit_sse_event("progress", {"pct": 0.5, "msg": "halfway"})
    assert "event: progress\n" in result
    assert "data: " in result
    assert '"pct": 0.5' in result

@pytest.mark.asyncio
async def test_progress_bridge():
    """ProgressBridge adapts sync callback to asyncio queue."""
    bridge = ProgressBridge()

    events = []

    async def drain():
        async for event in bridge.stream():
            events.append(event)

    drain_task = asyncio.create_task(drain())

    bridge.emit_progress(0.0, "start")
    bridge.emit_progress(0.5, "halfway")
    bridge.close()

    await drain_task

    assert len(events) == 2
    assert events[0]["type"] == "progress"
    assert events[0]["pct"] == 0.0
