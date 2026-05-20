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


@pytest.mark.asyncio
async def test_emit_error_envelope_matches_rest_shape():
    """SSE error event uses 'error' key (not 'detail') with request_id field."""
    bridge = ProgressBridge()
    bridge.emit_error("params_invalid", "bad params", {"field": "x"})
    bridge.close()

    events = []
    async for event in bridge.stream():
        events.append(event)

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "error" in events[0]
    assert "detail" not in events[0]
    assert events[0]["error"]["code"] == "params_invalid"
    assert events[0]["error"]["message"] == "bad params"
    assert events[0]["error"]["details"] == {"field": "x"}
    assert "request_id" in events[0]["error"]


@pytest.mark.asyncio
async def test_terminal_events_guaranteed_when_queue_full():
    """When queue is saturated with progress, terminal events still get through."""
    bridge = ProgressBridge()

    # Fill queue past maxsize (128) WITHOUT consuming. Each emit_progress
    # schedules a put on the loop; after we yield once (await asyncio.sleep(0)),
    # the loop drains scheduled callbacks. After 200 emits the queue contains
    # 128 progress events; the remaining 72 are dropped (counted in
    # _dropped_progress).
    for i in range(200):
        bridge.emit_progress(i / 200, f"step {i}")
    await asyncio.sleep(0)  # let scheduled puts run

    # Queue is at capacity. Terminal events must STILL get through by
    # displacing the oldest progress event.
    bridge.emit_done({"n_items": 200})
    bridge.close()
    await asyncio.sleep(0)

    events = []
    async for event in bridge.stream():
        events.append(event)

    # Some progress events were dropped, but the done event must be present
    # and is the last event before the sentinel terminates the stream.
    assert events[-1]["type"] == "done"
    assert events[-1]["result"] == {"n_items": 200}
    assert bridge._dropped_progress > 0
