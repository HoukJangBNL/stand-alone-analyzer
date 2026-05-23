# tests/api/test_run_fake_sse.py
import asyncio
import json

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.logging_ctx import RequestIdMiddleware
from flake_analysis.api.sse import ProgressBridge, emit_sse_event


@pytest.mark.asyncio
async def test_fake_step_sse(tmp_path, monkeypatch):
    """Fake step emits progress events over SSE.

    Sanity test for the SSE plumbing (ProgressBridge / emit_sse_event /
    StreamingResponse) — exercises the same wire format the production
    /run/* endpoints rely on. Mounted on a mini-app at the per-scan URL
    (W10-C.4b) so it parallels the real route grammar.
    """
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))

    fake_router = APIRouter()

    @fake_router.post("/projects/{project_id}/scans/{scan_id}/run/fake")
    async def run_fake(project_id: str, scan_id: int):
        """Fake step that emits 3 progress events."""
        bridge = ProgressBridge()

        async def generate():
            def worker():
                bridge.emit_progress(0.0, "start")
                bridge.emit_progress(0.5, "halfway")
                bridge.emit_progress(1.0, "done")
                bridge.emit_done({"n_items": 3})
                bridge.close()

            loop = asyncio.get_event_loop()
            loop.run_in_executor(None, worker)

            async for event in bridge.stream():
                yield emit_sse_event(event["type"], event)

        return StreamingResponse(generate(), media_type="text/event-stream")

    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(fake_router, prefix="/api/v1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with client.stream(
            "POST", "/api/v1/projects/local/scans/42/run/fake"
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]

            events = []
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    events.append(data)

            assert len(events) == 4
            assert events[0]["type"] == "progress"
            assert events[0]["pct"] == 0.0
            assert events[-1]["type"] == "done"
