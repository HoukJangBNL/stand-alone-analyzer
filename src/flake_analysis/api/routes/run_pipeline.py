"""W13 Pipeline orchestrator: POST /run/pipeline (P5.2).

Single SSE endpoint that drives all 5 pipeline steps over one connection,
emitting a multi-step event vocabulary distinct from the per-step routes.

Execution graph::

    thumbnails → background → sam → (domain_stats || domain_proximity) → pipeline_done

If background params differ from the persisted ``Analysis.background_params``,
a cascade fires *before* background runs (see services.cascade). The cascade
summary is included in the terminal ``pipeline_done`` payload.

Usage emission policy
---------------------
For v1, a single ``scan_run`` event is emitted per step at step boundaries
(matches the per-step routes' attribution model — admin_usage reports time
per step). Thumbnails is included for parity with per-step thumbnails route
even though it has no Run row.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import (
    get_db_session,
    get_manifest,
    get_session_for_background,
)
from flake_analysis.api.mutex import acquire_scan_lock
from flake_analysis.api.schemas.compute import (
    BackgroundParams,
    DomainProximityParams,
    DomainStatsParams,
    SamParams,
    ThumbnailsParams,
)
from flake_analysis.api.services.analyses import get_or_create_default_analysis
from flake_analysis.api.services.cascade import apply_background_cascade_if_needed
from flake_analysis.api.services.runs import record_run_end, record_run_start
from flake_analysis.api.services.usage import emit as usage_emit
from flake_analysis.api.sse import PipelineProgressBridge, sse_stream
from flake_analysis.db.models import Analysis
from flake_analysis.pipeline.background import run_background_step
from flake_analysis.pipeline.domain_proximity import run_domain_proximity_step
from flake_analysis.pipeline.domain_stats import run_domain_stats_step
from flake_analysis.pipeline.sam import run_sam_step
from flake_analysis.pipeline.thumbnails import run_thumbnails_step


router = APIRouter(
    prefix="/projects/{project_id}/scans/{scan_id}/run", tags=["run"]
)


class PipelineBody(BaseModel):
    """POST /run/pipeline body — one block of params per step."""

    thumbnails: ThumbnailsParams = Field(default_factory=ThumbnailsParams)
    background: BackgroundParams = Field(default_factory=BackgroundParams)
    sam: SamParams  # required: no default for weights_path
    domain_stats: DomainStatsParams = Field(default_factory=DomainStatsParams)
    domain_proximity: DomainProximityParams = Field(default_factory=DomainProximityParams)


# ---------------------------------------------------------------------------
# Step driver helpers
# ---------------------------------------------------------------------------


_STEP_INDEX = {
    "thumbnails": 0,
    "background": 1,
    "sam": 2,
    "domain_stats": 3,
    "domain_proximity": 4,
}


class _StepFailure(Exception):
    """Marker exception that carries the failing step name through gather()."""

    def __init__(self, step: str, original: BaseException):
        self.step = step
        self.original = original
        super().__init__(f"step {step!r} failed: {original}")


async def _emit_usage(
    user: User,
    *,
    step: str,
    project_id: str,
    scan_id: int,
) -> None:
    """Write a scan_run usage event in its own bg session."""
    async with get_session_for_background() as bg:
        await usage_emit(
            bg,
            user,
            "scan_run",
            {"step": step, "project_id": project_id, "scan_id": scan_id},
        )
        await bg.commit()


async def _start_run_row(*, analysis_id: int, step: str) -> int:
    """Insert a 'running' Run row in its own bg session and return run_id."""
    async with get_session_for_background() as bg:
        run_id = await record_run_start(bg, analysis_id=analysis_id, step=step)
        await bg.commit()
        return run_id


async def _finalize_run_row(
    *, run_id: int, status: str, metrics: dict | None = None, error: str | None = None
) -> None:
    """Update a Run row to its terminal status in its own bg session."""
    async with get_session_for_background() as bg:
        await record_run_end(
            bg, run_id=run_id, status=status, metrics=metrics, error=error
        )
        await bg.commit()


async def _mark_step_done(*, analysis_id: int, step: str) -> None:
    """Set steps_done[step]=True on the Analysis in its own bg session."""
    async with get_session_for_background() as bg:
        a = await bg.get(Analysis, analysis_id)
        if a is None:
            return
        a.steps_done = {**(a.steps_done or {}), step: True}
        await bg.commit()


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/pipeline")
async def run_pipeline(
    project_id: str,
    scan_id: int,
    body: PipelineBody,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Drive all 5 pipeline steps over one SSE stream.

    Pre-driver phase (request session): bootstrap default Analysis, apply
    cascade rule, persist. Acquire per-scan lock; failures here surface as
    HTTP 423 / 5xx envelopes (not SSE).

    Driver phase (background sessions): each step opens its own short-lived
    session for runs/audit + steps_done updates, runs the wrapper in an
    executor, and emits step_started/step_progress/step_completed events.
    domain_stats and domain_proximity run in parallel; if either fails the
    other is allowed to finish then the pipeline_error fires.
    """
    manifest = await get_manifest(project_id=project_id, scan_id=scan_id)

    analysis = await get_or_create_default_analysis(session, scan_id=scan_id)
    cascade_summary = await apply_background_cascade_if_needed(
        session,
        analysis=analysis,
        new_background_params=body.background.model_dump(),
    )
    await session.commit()

    # Capture the analysis_id BEFORE the driver opens new bg sessions; the
    # ORM object loaded above may be expired/detached after the request
    # session yields.
    analysis_id = analysis.id

    # Acquire the per-scan lock synchronously so a contended request gets an
    # HTTP-level error envelope (ProjectBusy -> 423) instead of an SSE stream
    # that opens and immediately errors.
    lock_cm = acquire_scan_lock(scan_id)
    await lock_cm.__aenter__()

    bridge = PipelineProgressBridge()

    async def _run_step_with_runs_row(
        *,
        name: str,
        sync_callable,
        metrics_factory,
    ):
        """Run one of the 4 audited steps (background/sam/stats/proximity).

        Emits step_started → usage → record_run_start → executor → record_run_end →
        steps_done update → step_completed. Raises :class:`_StepFailure` on error
        after marking the Run row 'failed'.
        """
        bridge.step_started(name, _STEP_INDEX[name])
        await _emit_usage(user, step=name, project_id=project_id, scan_id=scan_id)
        run_id = await _start_run_row(analysis_id=analysis_id, step=name)
        try:
            result = await asyncio.get_running_loop().run_in_executor(None, sync_callable)
        except BaseException as e:  # noqa: BLE001 — re-raised as _StepFailure
            await _finalize_run_row(run_id=run_id, status="failed", error=str(e))
            raise _StepFailure(name, e) from e
        await _finalize_run_row(
            run_id=run_id, status="completed", metrics=metrics_factory(result)
        )
        await _mark_step_done(analysis_id=analysis_id, step=name)
        bridge.step_completed(name, result)
        return result

    async def _run_thumbnails():
        """Thumbnails has no Run row by design (CHECK constraint)."""
        bridge.step_started("thumbnails", _STEP_INDEX["thumbnails"])
        await _emit_usage(user, step="thumbnails", project_id=project_id, scan_id=scan_id)

        def _call():
            return run_thumbnails_step(
                analysis_folder=manifest.analysis_folder,
                raw_images_dir=manifest.raw_images_dir,
                raw_ext=body.thumbnails.raw_ext,
                quality=body.thumbnails.quality,
                force_recompute=body.thumbnails.force_recompute,
                progress_callback=lambda p, m: bridge.step_progress("thumbnails", p, m),
            )

        try:
            result = await asyncio.get_running_loop().run_in_executor(None, _call)
        except BaseException as e:  # noqa: BLE001
            raise _StepFailure("thumbnails", e) from e
        await _mark_step_done(analysis_id=analysis_id, step="thumbnails")
        bridge.step_completed("thumbnails", result)
        return result

    async def _run_background():
        def _call():
            return run_background_step(
                raw_images_dir=manifest.raw_images_dir,
                analysis_folder=manifest.analysis_folder,
                seed=body.background.seed,
                max_images=body.background.max_images,
                gaussian_sigma=body.background.gaussian_sigma,
                method=body.background.method,
                progress_callback=lambda p, m: bridge.step_progress("background", p, m),
            )

        return await _run_step_with_runs_row(
            name="background",
            sync_callable=_call,
            metrics_factory=lambda r: {
                "max_images": body.background.max_images,
                "method": body.background.method,
            },
        )

    async def _run_sam():
        def _call():
            return run_sam_step(
                raw_images_dir=manifest.raw_images_dir,
                analysis_folder=manifest.analysis_folder,
                weights_path=body.sam.weights_path,
                device=body.sam.device,
                progress_callback=lambda p, m: bridge.step_progress("sam", p, m),
            )

        return await _run_step_with_runs_row(
            name="sam",
            sync_callable=_call,
            metrics_factory=lambda r: {
                "images": r.get("images") if isinstance(r, dict) else None,
                "masks_total": r.get("masks_total") if isinstance(r, dict) else None,
                "errors": r.get("errors") if isinstance(r, dict) else None,
            },
        )

    async def _run_domain_stats():
        def _call():
            return run_domain_stats_step(
                raw_images_dir=manifest.raw_images_dir,
                annotations_path=manifest.annotations_path,
                analysis_folder=manifest.analysis_folder,
                repr_mode=body.domain_stats.repr_mode,
                raw_ext=body.domain_stats.raw_ext,
                progress_callback=lambda p, m: bridge.step_progress("domain_stats", p, m),
            )

        return await _run_step_with_runs_row(
            name="domain_stats",
            sync_callable=_call,
            metrics_factory=lambda r: {
                "repr_mode": body.domain_stats.repr_mode,
                "raw_ext": body.domain_stats.raw_ext,
            },
        )

    async def _run_domain_proximity():
        def _call():
            return run_domain_proximity_step(
                annotations_path=manifest.annotations_path,
                analysis_folder=manifest.analysis_folder,
                r_max_px=body.domain_proximity.r_max_px,
                min_area_px=body.domain_proximity.min_area_px,
                max_area_px=body.domain_proximity.max_area_px,
                d_touch_px=body.domain_proximity.d_touch_px,
                pixel_size_um=body.domain_proximity.pixel_size_um,
                link_distance_um=body.domain_proximity.link_distance_um,
                workers=body.domain_proximity.workers,
                progress_callback=lambda p, m: bridge.step_progress("domain_proximity", p, m),
            )

        return await _run_step_with_runs_row(
            name="domain_proximity",
            sync_callable=_call,
            metrics_factory=lambda r: {
                "r_max_px": body.domain_proximity.r_max_px,
                "workers": body.domain_proximity.workers,
            },
        )

    async def driver():
        try:
            await _run_thumbnails()
            await _run_background()
            await _run_sam()
            # domain_stats and domain_proximity run in parallel.
            await asyncio.gather(_run_domain_stats(), _run_domain_proximity())
            bridge.pipeline_done({"cascade": cascade_summary})
        except _StepFailure as sf:
            bridge.pipeline_error(
                step=sf.step,
                code=type(sf.original).__name__,
                message=str(sf.original),
                details={"exc_type": type(sf.original).__name__},
            )
        except BaseException as e:  # noqa: BLE001 — surface unexpected failures
            bridge.pipeline_error(
                step="unknown",
                code=type(e).__name__,
                message=str(e),
                details={"exc_type": type(e).__name__},
            )
        finally:
            await lock_cm.__aexit__(None, None, None)

    async def generate():
        task = asyncio.create_task(driver())
        try:
            async for frame in sse_stream(bridge):
                yield frame
        finally:
            await task

    return StreamingResponse(generate(), media_type="text/event-stream")
