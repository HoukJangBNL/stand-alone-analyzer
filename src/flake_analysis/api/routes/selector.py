"""Selector routes per backend design §1.2 + frontend design §4.2.

POST /run/selector — SSE.
POST /selector/commit — synchronous JSON.
"""
from __future__ import annotations
import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_manifest
from flake_analysis.api.mutex import acquire_project_lock
from flake_analysis.api.schemas.selector import (
    SelectorParams,
    SelectorCommitRequest,
    SelectorCommitSummary,
)
from flake_analysis.api.services.selector_service import apply_brush_intersection
from flake_analysis.api.sse import ProgressBridge, emit_sse_event
from flake_analysis.state.manifest import Manifest
from flake_analysis.pipeline.selector import run_selector_step

router = APIRouter(prefix="/projects/{project_id}", tags=["selector"])


@router.post("/run/selector")
async def run_selector(
    project_id: str,
    params: SelectorParams,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Run selector pipeline step with SSE progress (writes selection.parquet)."""
    # Lock+drain pattern: acquire synchronously so contention surfaces as
    # HTTP 423 ProjectBusy, then drain in the generator's finally.
    lock_cm = acquire_project_lock(project_id)
    await lock_cm.__aenter__()

    bridge = ProgressBridge()

    def call_wrapper():
        return run_selector_step(
            analysis_folder=manifest.analysis_folder,
            area_min=params.area_min,
            area_max=params.area_max,
            std_r_min=params.std_r_min,
            std_r_max=params.std_r_max,
            std_g_min=params.std_g_min,
            std_g_max=params.std_g_max,
            std_b_min=params.std_b_min,
            std_b_max=params.std_b_max,
            sam2_min=params.sam2_min,
            sam2_max=params.sam2_max,
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


@router.post("/selector/commit")
async def commit_selection(
    project_id: str,
    body: SelectorCommitRequest,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
) -> SelectorCommitSummary:
    """Run selector pipeline + apply brush intersection. Synchronous JSON."""
    async with acquire_project_lock(project_id):
        loop = asyncio.get_running_loop()

        def _call():
            return run_selector_step(
                analysis_folder=manifest.analysis_folder,
                area_min=body.params.area_min,
                area_max=body.params.area_max,
                std_r_min=body.params.std_r_min,
                std_r_max=body.params.std_r_max,
                std_g_min=body.params.std_g_min,
                std_g_max=body.params.std_g_max,
                std_b_min=body.params.std_b_min,
                std_b_max=body.params.std_b_max,
                sam2_min=body.params.sam2_min,
                sam2_max=body.params.sam2_max,
                progress_callback=None,
            )

        result = await loop.run_in_executor(None, _call)
        out_path = Path(str(result["output_path"]))
        n_filter_accepted = int(result["selected_count"])
        total_count = int(result["total_count"])

        n_committed = await loop.run_in_executor(
            None,
            lambda: apply_brush_intersection(out_path, lasso_ids=body.lasso_ids),
        )

        return SelectorCommitSummary(
            output_path=str(out_path),
            n_committed=n_committed,
            n_filter_accepted=n_filter_accepted,
            n_lasso=len(body.lasso_ids) if body.lasso_ids else 0,
            total_count=total_count,
            params_hash=result.get("params_hash"),
        )
