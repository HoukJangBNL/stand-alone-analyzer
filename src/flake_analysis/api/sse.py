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
        self._loop = asyncio.get_running_loop()
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


SSE_HEARTBEAT_SECONDS: float = 15.0
"""Heartbeat interval. Floor is 15s — lowering requires ops signoff."""

SSE_HEARTBEAT_FRAME: str = ": heartbeat\n\n"
"""SSE comment line. EventSource clients ignore lines starting with ':' per WHATWG."""


class PipelineProgressBridge:
    """Multi-step variant of :class:`ProgressBridge` for the W13 pipeline orchestrator.

    Emits a wider event vocabulary distinct from the per-step routes so the
    React frontend can drive a 5-step indicator from a single SSE stream:

    * ``step_started``  — non-terminal: ``{step, index, total}``
    * ``step_progress`` — non-terminal: ``{step, pct, msg}``
    * ``step_completed``— non-terminal: ``{step, result}``
    * ``pipeline_done`` — terminal: ``{cascade, ...}`` then sentinel
    * ``pipeline_error``— terminal: ``{step, error: {code, message, details, request_id}}`` then sentinel

    Drop-vs-priority semantics mirror :class:`ProgressBridge`: progress events
    are silently dropped when the consumer falls behind, terminal events are
    guaranteed to be delivered. Construct only inside a running event loop;
    step-emitter methods may be called from sync executor threads (the
    progress shim closes over the bridge), so all enqueues hop through
    ``loop.call_soon_threadsafe``.

    The ``_queue`` attribute is exposed for :func:`sse_stream` (which reads
    it directly) — keeping the same contract as :class:`ProgressBridge`.
    """

    def __init__(self):
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=128)
        self._loop = asyncio.get_running_loop()
        self._dropped_progress = 0

    def _put_progress(self, event: dict) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped_progress += 1

    def _put_terminal(self, event: dict | None) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._queue.put_nowait(event)

    def step_started(self, step: str, index: int, total: int = 5) -> None:
        event = {"type": "step_started", "step": step, "index": index, "total": total}
        self._loop.call_soon_threadsafe(self._put_progress, event)

    def step_progress(self, step: str, pct: float, msg: str) -> None:
        event = {"type": "step_progress", "step": step, "pct": pct, "msg": msg}
        self._loop.call_soon_threadsafe(self._put_progress, event)

    def step_completed(self, step: str, result: dict) -> None:
        event = {"type": "step_completed", "step": step, "result": result}
        self._loop.call_soon_threadsafe(self._put_progress, event)

    def pipeline_done(self, summary: dict) -> None:
        """Terminal. Enqueues ``pipeline_done`` and the sentinel."""
        event = {"type": "pipeline_done", **summary}
        self._loop.call_soon_threadsafe(self._put_terminal, event)
        self.close()

    def pipeline_error(
        self,
        step: str,
        code: str,
        message: str,
        details: dict | None = None,
    ) -> None:
        """Terminal. Mirrors :meth:`ProgressBridge.emit_error` envelope shape."""
        from flake_analysis.api.logging_ctx import get_request_id

        request_id = get_request_id() or ""
        event = {
            "type": "pipeline_error",
            "step": step,
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "request_id": request_id,
            },
        }
        self._loop.call_soon_threadsafe(self._put_terminal, event)
        self.close()

    def close(self) -> None:
        """Signal end of stream. Guaranteed delivery."""
        self._loop.call_soon_threadsafe(self._put_terminal, None)


async def sse_stream(
    bridge: "ProgressBridge | PipelineProgressBridge",
    heartbeat_seconds: float | None = None,
) -> AsyncGenerator[str, None]:
    """Drain a ProgressBridge into SSE wire frames, injecting heartbeats while idle.

    On every ``heartbeat_seconds`` interval where no real event arrives, emit
    ``: heartbeat\\n\\n`` so intermediaries (nginx / ELB) keep the connection
    alive. Real events are encoded with :func:`emit_sse_event` exactly as the
    inline drain loops did before this helper existed.

    The interval is resolved at call time via the module-level
    ``SSE_HEARTBEAT_SECONDS`` constant (so tests can ``monkeypatch.setattr``
    it). Pass an explicit ``heartbeat_seconds`` to override per-call.
    """
    interval = heartbeat_seconds if heartbeat_seconds is not None else SSE_HEARTBEAT_SECONDS
    # Read from the queue directly: wrapping bridge.stream().__anext__() in
    # asyncio.wait_for cancels the underlying queue.get() on timeout, which
    # closes the async generator and drops the next event. asyncio.Queue.get()
    # is cancellation-safe, so we can re-enter wait_for cleanly.
    #
    # Hot-path optimisation: when the queue already has an item, fetch it
    # without going through wait_for (which allocates a Task + TimerHandle
    # per call). This preserves the concurrent-drain behaviour that the
    # producer thread relies on when blasting >128 events through the
    # bounded (maxsize=128) queue.
    queue = bridge._queue
    while True:
        if not queue.empty():
            event = queue.get_nowait()
        else:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=interval)
            except asyncio.TimeoutError:
                yield SSE_HEARTBEAT_FRAME
                continue
        if event is None:
            return
        yield emit_sse_event(event["type"], event)
