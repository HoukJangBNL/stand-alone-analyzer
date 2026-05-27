"""Default-Analysis bootstrap for the pipeline orchestrator (P5.2).

The W13 ``POST /run/pipeline`` endpoint expects an ``Analysis`` row to drive
runs/audit logging. Pre-Phase-4 there is no UI surface for choosing a SAM
weight, so we materialise a default Analysis on first run using the first
``Model`` row registered in the catalog as fallback.

Phase 4 P4.1 will register the production SAM weight and add proper
provenance — until then "first available" is the documented contract.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.db.models import Analysis, Model

# Default AMG (Automatic Mask Generator) parameters used when bootstrapping a
# fresh Analysis row from the orchestrator. These mirror the defaults used by
# the pre-PG Streamlit pipeline; tuning is a Phase 4 concern.
DEFAULT_AMG_PARAMS: dict = {
    "points_per_side": 32,
    "pred_iou_thresh": 0.88,
    "stability_score_thresh": 0.95,
    "crop_n_layers": 0,
    "min_mask_region_area": 0,
}

DEFAULT_LINK_DISTANCE_PX: float = 200.0


async def get_or_create_default_analysis(
    session: AsyncSession,
    *,
    scan_id: int,
) -> Analysis:
    """Return the first Analysis for ``scan_id`` or create one bound to the first Model.

    Raises ``RuntimeError`` if no Model is registered (Phase 4 P4.1 prerequisite).
    """
    existing = (
        await session.execute(
            select(Analysis).where(Analysis.scan_id == scan_id).order_by(Analysis.created_at).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    default_model = (
        await session.execute(select(Model).order_by(Model.id).limit(1))
    ).scalar_one_or_none()
    if default_model is None:
        raise RuntimeError(
            "no SAM model registered — Phase 4 P4.1 prerequisite: insert a Model row before running pipeline"
        )

    analysis = Analysis(
        scan_id=scan_id,
        model_id=default_model.id,
        amg_params=DEFAULT_AMG_PARAMS,
        link_distance_px=DEFAULT_LINK_DISTANCE_PX,
        steps_done={},
    )
    session.add(analysis)
    await session.flush()
    return analysis
