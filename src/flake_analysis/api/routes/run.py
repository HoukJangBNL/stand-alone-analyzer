"""Compute run endpoints (SSE) per backend design §1.2."""
from __future__ import annotations
import asyncio
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_manifest
from flake_analysis.api.mutex import acquire_project_lock
from flake_analysis.api.sse import ProgressBridge, emit_sse_event
from flake_analysis.api.schemas.compute import ThumbnailsParams
from flake_analysis.state.manifest import Manifest
from flake_analysis.pipeline.thumbnails import run_thumbnails_step

router = APIRouter(prefix="/projects/{project_id}/run", tags=["run"])

@router.post("/thumbnails")
async def run_thumbnails(
    project_id: str,
    params: ThumbnailsParams,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Run thumbnails step with SSE progress."""
    # Acquire the project lock synchronously so a contended request gets an
    # HTTP-level error envelope (ProjectBusy -> 423) instead of an SSE stream
    # that opens and immediately errors. The lock must be held for the lifetime
    # of the generator, so we enter the context manager manually here and exit
    # it in the generator's finally block.
    lock_cm = acquire_project_lock(project_id)
    await lock_cm.__aenter__()

    bridge = ProgressBridge()

    def call_wrapper():
        return run_thumbnails_step(
            analysis_folder=manifest.analysis_folder,
            raw_images_dir=manifest.raw_images_dir,
            raw_ext=params.raw_ext,
            quality=params.quality,
            force_recompute=params.force_recompute,
            progress_callback=bridge.emit_progress,
        )

    async def generate():
        loop = asyncio.get_running_loop()

        async def run_pipeline():
            try:
                result = await loop.run_in_executor(None, call_wrapper)
                bridge.emit_done(result)
            except Exception as e:
                bridge.emit_error("pipeline_failed", str(e), {"exc_type": type(e).__name__})
            finally:
                bridge.close()

        task = asyncio.create_task(run_pipeline())
        try:
            async for event in bridge.stream():
                yield emit_sse_event(event["type"], event)
        finally:
            try:
                await task
            finally:
                await lock_cm.__aexit__(None, None, None)

    return StreamingResponse(generate(), media_type="text/event-stream")
