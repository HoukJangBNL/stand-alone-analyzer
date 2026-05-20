# src/flake_analysis/api/sse.py
"""SSE helpers per backend design §2."""
from __future__ import annotations
import asyncio
import json
from typing import Any, AsyncGenerator

def emit_sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format an SSE event (event: + data: lines)."""
    json_data = json.dumps(data)
    return f"event: {event_type}\ndata: {json_data}\n\n"

class ProgressBridge:
    """Adapts sync ProgressCallback to asyncio queue for SSE streaming."""

    def __init__(self):
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=64)
        self._loop = asyncio.get_event_loop()

    def emit_progress(self, pct: float, msg: str):
        """Called from sync context (worker thread). Thread-safe put."""
        event = {"type": "progress", "pct": pct, "msg": msg}
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    def emit_done(self, result: dict):
        """Emit terminal 'done' event."""
        event = {"type": "done", "result": result}
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    def emit_error(self, code: str, message: str, details: dict | None = None):
        """Emit terminal 'error' event."""
        event = {
            "type": "error",
            "detail": {
                "code": code,
                "message": message,
                "details": details or {},
            }
        }
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    def close(self):
        """Signal end of stream."""
        self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

    async def stream(self) -> AsyncGenerator[dict, None]:
        """Async generator that yields events until closed."""
        while True:
            event = await self._queue.get()
            if event is None:
                break
            yield event
