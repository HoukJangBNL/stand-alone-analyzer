"""Background-params cascade rule for the pipeline orchestrator (P5.2).

Per db-schema-v6 §10 Convention #4 + Phase 0 decision 8: when the pipeline
runs with a ``background.*`` body that differs from the persisted
``Analysis.background_params``, downstream artefacts are stale. Apply the
cascade *before* running background:

1. Persist the new background_params on the Analysis.
2. Drop ``sam`` / ``domain_stats`` / ``domain_proximity`` keys from
   ``steps_done`` (keep ``thumbnails`` / ``background`` markers if any).
3. Delete all ``Domain`` and ``Flake`` rows scoped to the analysis_id.

The orchestrator passes the resulting summary into the ``pipeline_done``
SSE event so the client can surface "we re-ran SAM because background
changed" UX.
"""
from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.db.models import Analysis
from flake_analysis.db.models.sam import Domain, Flake


# Steps that depend on the background image; cleared when background_params change.
_DEPENDENT_STEPS: tuple[str, ...] = ("sam", "domain_stats", "domain_proximity")


async def apply_background_cascade_if_needed(
    session: AsyncSession,
    *,
    analysis: Analysis,
    new_background_params: dict,
) -> dict:
    """Compare new params against persisted; cascade if different.

    Returns a summary dict embedded in ``pipeline_done``:

    * ``{"fired": False}`` — params unchanged, nothing cleared.
    * ``{"fired": True, "cleared_steps": [...]}`` — params persisted,
      steps_done pruned, Domain/Flake rows deleted.

    Caller is responsible for committing the session.
    """
    persisted = analysis.background_params or {}
    if persisted == new_background_params:
        return {"fired": False}

    # 1. Update background_params on the analysis row.
    analysis.background_params = dict(new_background_params)

    # 2. Prune steps_done.
    pruned: dict = {k: v for k, v in (analysis.steps_done or {}).items() if k not in _DEPENDENT_STEPS}
    # Reassign so SQLAlchemy detects the dirty JSONB.
    analysis.steps_done = pruned

    # 3. Delete dependent rows scoped to the analysis.
    await session.execute(delete(Domain).where(Domain.analysis_id == analysis.id))
    await session.execute(delete(Flake).where(Flake.analysis_id == analysis.id))

    return {"fired": True, "cleared_steps": list(_DEPENDENT_STEPS)}
