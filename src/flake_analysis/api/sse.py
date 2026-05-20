# src/flake_analysis/api/sse.py
"""SSE helpers per backend design §2."""
from __future__ import annotations
import asyncio
import json
import logging
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)


def emit_sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format an SSE event (event: + data: lines)."""
    json_data = json.dumps(data)
    return f"event: {event_type}\ndata: {json_data}\n\n"


class ProgressBridge:
    """Adapts sync ProgressCallback to asyncio queue for SSE streaming.

    Construct only inside a running event loop (e.g., from within an async route handler).
    Progress events may be dropped silently when the consumer falls behind, but
    terminal events (done / error / sentinel) are guaranteed to be delivered.
    """

    def __init__(self):
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=128)
        self._loop = asyncio.get_event_loop()
        self._dropped_progress = 0

    def _put_progress(self, event: dict) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped_progress += 1

    def _put_terminal(self, event: dict | None) -> None:
        # Terminal events MUST be delivered. If the queue is full, drop the
        # oldest item and retry — terminal events take priority over any single
        # progress event.
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._queue.put_nowait(event)

    def emit_progress(self, pct: float, msg: str) -> None:
        """Called from sync context (worker thread). Thread-safe put. Drops silently if full."""
        event = {"type": "progress", "pct": pct, "msg": msg}
        self._loop.call_soon_threadsafe(self._put_progress, event)

    def emit_done(self, result: dict) -> None:
        """Emit terminal 'done' event. Guaranteed delivery."""
        event = {"type": "done", "result": result}
        self._loop.call_soon_threadsafe(self._put_terminal, event)

    def emit_error(self, code: str, message: str, details: dict | None = None) -> None:
        """Emit terminal 'error' event. Shape mirrors errors.py REST envelope. Guaranteed delivery."""
        from flake_analysis.api.logging_ctx import get_request_id
        request_id = get_request_id() or ""
        event = {
            "type": "error",
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "request_id": request_id,
            },
        }
        self._loop.call_soon_threadsafe(self._put_terminal, event)

    def close(self) -> None:
        """Signal end of stream. Guaranteed delivery."""
        self._loop.call_soon_threadsafe(self._put_terminal, None)

    async def stream(self) -> AsyncGenerator[dict, None]:
        """Async generator that yields events until closed."""
        while True:
            event = await self._queue.get()
            if event is None:
                break
            yield event
