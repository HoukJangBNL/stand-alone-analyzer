"""Clustering routes per backend design §1.2.

POST /run/clustering/refit — SSE (expensive: GMM EM).
POST /run/clustering/apply_thresholds — SSE (cheap: parquet rewrite).
Both share the same per-project mutex (acquire_project_lock(pid)).
"""
from __future__ import annotations
import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_manifest
from flake_analysis.api.mutex import acquire_project_lock
from flake_analysis.api.schemas.clustering import (
    ApplyThresholdsParams,
    ClusteringRefitParams,
)
from flake_analysis.api.sse import ProgressBridge, emit_sse_event
from flake_analysis.pipeline.clustering import apply_thresholds, run_clustering_step
from flake_analysis.state.manifest import Manifest

router = APIRouter(prefix="/projects/{project_id}", tags=["clustering"])


@router.post("/run/clustering/refit")
async def run_clustering_refit(
    project_id: str,
    params: ClusteringRefitParams,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Fit GMM with manual seed groups (SSE). Lock+drain pattern."""
    lock_cm = acquire_project_lock(project_id)
    await lock_cm.__aenter__()

    bridge = ProgressBridge()
    seed_groups_payload: list[dict[str, Any]] = [
        {"name": sg.name, "domain_ids": sg.domain_ids} for sg in params.seed_groups
    ]

    def call_wrapper():
        return run_clustering_step(
            analysis_folder=manifest.analysis_folder,
            seed_groups=seed_groups_payload,
            feature_cols=params.feature_cols,
            covariance_type=params.covariance_type,
            rgb_threshold=params.rgb_threshold,
            fit_scope=params.fit_scope,
            max_mahalanobis=params.max_mahalanobis,
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
