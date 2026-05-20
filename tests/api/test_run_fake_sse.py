# tests/api/test_run_fake_sse.py
import pytest
import asyncio
import json
from httpx import AsyncClient, ASGITransport
from flake_analysis.api.main import create_app
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from flake_analysis.api.sse import ProgressBridge, emit_sse_event


@pytest.mark.asyncio
async def test_fake_step_sse(tmp_path):
    """Fake step emits progress events over SSE."""
    import os
    analysis_folder = tmp_path / "proj"
    analysis_folder.mkdir()

    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis_folder)

    fake_router = APIRouter()

    @fake_router.post("/projects/{project_id}/run/fake")
    async def run_fake(project_id: str):
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

    app = create_app()
    app.include_router(fake_router, prefix="/api/v1")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with client.stream("POST", "/api/v1/projects/local/run/fake") as resp:
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

    os.environ.pop("SAA_ANALYSIS_FOLDER", None)
