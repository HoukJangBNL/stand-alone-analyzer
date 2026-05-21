"""Clustering routes per backend design §1.2.

POST /run/clustering/refit — SSE (expensive: GMM EM).
POST /run/clustering/apply_thresholds — SSE (cheap: parquet rewrite).
Both share the same per-project mutex (acquire_project_lock(pid)).
"""
from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_manifest
from flake_analysis.api.mutex import acquire_project_lock
from flake_analysis.api.schemas.clustering import (
    ApplyThresholdsParams,
    ClusteringRefitParams,
)
from flake_analysis.api.sse import ProgressBridge, sse_stream
from flake_analysis.core.clustering.auto_opt import auto_tune_reg_covar
from flake_analysis.pipeline.clustering import apply_thresholds, run_clustering_step
from flake_analysis.state.manifest import Manifest

router = APIRouter(prefix="/projects/{project_id}", tags=["clustering"])


def _build_auto_tune_inputs(
    analysis_folder: str,
    seed_groups_payload: list[dict[str, Any]],
) -> tuple[np.ndarray, list[list[int]]]:
    """Mirror pipeline/clustering.py preconditions to call auto_tune_reg_covar.

    Loads stats.npz + selection.parquet, restricts the RGB array to the
    selected domain ids, and remaps seed_groups[*].domain_ids to row positions
    inside that selected subset (the same indexing the engine receives).
    """
    af = Path(analysis_folder)
    npz = np.load(af / "02_domain_stats" / "stats.npz", allow_pickle=False)
    rgb_all = npz["repr_rgbs"]
    flake_ids_all = npz["flake_ids"].astype(np.int64)
    sel = pd.read_parquet(af / "03_selector" / "selection.parquet")
    selected_ids = (
        sel.loc[sel["selected"].astype(bool), "domain_id"].astype(int).to_numpy()
    )
    id_to_pos = {int(d): pos for pos, d in enumerate(flake_ids_all)}
    keep = [id_to_pos[int(d)] for d in selected_ids if int(d) in id_to_pos]
    rgb_sel = rgb_all[np.array(keep, dtype=np.int64)]
    sel_ids = np.array(
        [int(d) for d in selected_ids if int(d) in id_to_pos],
        dtype=np.int64,
    )
    sel_id_to_pos = {int(d): pos for pos, d in enumerate(sel_ids)}
    seed_pos: list[list[int]] = []
    for grp in seed_groups_payload:
        pos = [sel_id_to_pos[int(d)] for d in grp["domain_ids"] if int(d) in sel_id_to_pos]
        if pos:
            seed_pos.append(pos)
    return rgb_sel, seed_pos


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

    def call_wrapper(reg_covar: float):
        return run_clustering_step(
            analysis_folder=manifest.analysis_folder,
            seed_groups=seed_groups_payload,
            feature_cols=params.feature_cols,
            covariance_type=params.covariance_type,
            rgb_threshold=params.rgb_threshold,
            fit_scope=params.fit_scope,
            max_mahalanobis=params.max_mahalanobis,
            reg_covar=reg_covar,
            progress_callback=bridge.emit_progress,
        )

    async def generate():
        loop = asyncio.get_running_loop()

        async def run_pipeline():
            try:
                # When auto_tune=True, sweep the candidate set server-side and
                # use the optimiser's pick. Otherwise echo the schema value.
                if params.auto_tune:
                    rgb_sel, seed_pos = await loop.run_in_executor(
                        None,
                        _build_auto_tune_inputs,
                        manifest.analysis_folder,
                        seed_groups_payload,
                    )
                    chosen_reg_covar = float(
                        await loop.run_in_executor(
                            None, auto_tune_reg_covar, rgb_sel, seed_pos
                        )
                    )
                else:
                    chosen_reg_covar = float(params.reg_covar)
                result = await loop.run_in_executor(None, call_wrapper, chosen_reg_covar)
                # Coerce Path-valued keys from the core summary so the SSE JSON
                # encoder (json.dumps in emit_sse_event) doesn't choke.
                for k in ("labels_path", "assignments_path", "gmm_model_path"):
                    if k in result and result[k] is not None:
                        result[k] = str(result[k])
                result["reg_covar_chosen"] = chosen_reg_covar
                bridge.emit_done(result)
            except Exception as e:
                bridge.emit_error("pipeline_failed", str(e), {"exc_type": type(e).__name__})
            finally:
                bridge.close()

        task = asyncio.create_task(run_pipeline())
        try:
            async for frame in sse_stream(bridge):
                yield frame
        finally:
            try:
                await task
            finally:
                await lock_cm.__aexit__(None, None, None)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/run/clustering/apply_thresholds")
async def run_clustering_apply_thresholds(
    project_id: str,
    params: ApplyThresholdsParams,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Rewrite assignments.parquet with new thresholds + max_mahalanobis (SSE). Lock+drain."""
    lock_cm = acquire_project_lock(project_id)
    await lock_cm.__aenter__()

    bridge = ProgressBridge()

    def call_wrapper():
        return apply_thresholds(
            analysis_folder=manifest.analysis_folder,
            cluster_thresholds={int(k): float(v) for k, v in params.cluster_thresholds.items()},
            max_mahalanobis=params.max_mahalanobis,
        )

    async def generate():
        loop = asyncio.get_running_loop()

        async def run_pipeline():
            try:
                bridge.emit_progress(0.1, "Applying thresholds...")
                result = await loop.run_in_executor(None, call_wrapper)
                bridge.emit_progress(1.0, "Done")
                bridge.emit_done(result)
            except Exception as e:
                bridge.emit_error("pipeline_failed", str(e), {"exc_type": type(e).__name__})
            finally:
                bridge.close()

        task = asyncio.create_task(run_pipeline())
        try:
            async for frame in sse_stream(bridge):
                yield frame
        finally:
            try:
                await task
            finally:
                await lock_cm.__aexit__(None, None, None)

    return StreamingResponse(generate(), media_type="text/event-stream")
