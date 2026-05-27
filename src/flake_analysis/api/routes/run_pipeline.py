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
from flake_analysis.api.sse_listen import listen_for_run
from flake_analysis.db.models import Analysis
from flake_analysis.pipeline.background import run_background_step
from flake_analysis.pipeline.domain_proximity import run_domain_proximity_step
from flake_analysis.pipeline.domain_stats import run_domain_stats_step
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
# Worker-queue seams (P4.2.d). The SAM step is deferred to a procrastinate
# worker on the ``gpu`` queue; CPU steps stay in-process per Phase 4 D5.
# These are module-level functions so tests can monkeypatch them with
# fakes that synthesise the worker→API NOTIFY stream without touching a
# real queue.
# ---------------------------------------------------------------------------


async def _defer_sam_job(
    *,
    run_id: int,
    raw_images_dir,
    analysis_folder,
    weights_path,
    device: str | None,
) -> None:
    """Push a SAM job onto the procrastinate ``gpu`` queue."""
    from flake_analysis.worker import tasks as _tasks  # noqa: F401 — register tasks
    from flake_analysis.worker.app import app

    await app.tasks["run_sam"].defer_async(
        run_id=run_id,
        raw_images_dir=str(raw_images_dir),
        analysis_folder=str(analysis_folder),
        weights_path=str(weights_path),
        device=device,
    )


def _stream_sam_events(run_id: int):
    """Yield decoded NOTIFY payloads from the worker's progress channel."""
    return listen_for_run(run_id)


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
        """SAM step: defer to worker queue, listen for NOTIFY fan-out (P4.2).

        Mirrors :func:`_run_step_with_runs_row` for the CPU steps, but the
        executor is replaced by:

            defer_async(...) → async for payload in listen_for_run(run_id):

        Translates each ``progress`` payload to ``bridge.step_progress``
        (preserving the SSE wire format) and lands on a ``completed`` /
        ``error`` terminal. ``error`` raises :class:`_StepFailure` so the
        orchestrator's gather/sequence handles it identically to a CPU
        step crashing in the executor.
        """
        bridge.step_started("sam", _STEP_INDEX["sam"])
        await _emit_usage(user, step="sam", project_id=project_id, scan_id=scan_id)
        run_id = await _start_run_row(analysis_id=analysis_id, step="sam")

        await _defer_sam_job(
            run_id=run_id,
            raw_images_dir=manifest.raw_images_dir,
            analysis_folder=manifest.analysis_folder,
            weights_path=body.sam.weights_path,
            device=body.sam.device,
        )

        terminal_result: dict | None = None
        terminal_error: tuple[str, str] | None = None  # (code, message)
        async for payload in _stream_sam_events(run_id):
            ptype = payload.get("type")
            if ptype == "progress":
                bridge.step_progress(
                    "sam",
                    float(payload.get("progress", 0.0)),
                    str(payload.get("message", "")),
                )
            elif ptype == "completed":
                terminal_result = payload.get("result", {}) or {}
                break
            elif ptype == "error":
                terminal_error = (
                    str(payload.get("code") or "WorkerError"),
                    str(payload.get("message") or ""),
                )
                break

        if terminal_error is not None:
            code, message = terminal_error
            await _finalize_run_row(run_id=run_id, status="failed", error=message)
            # _StepFailure carries the original exception class on .original;
            # we don't have one here, so synthesise an exception whose class
            # name matches ``code`` so the orchestrator's pipeline_error
            # envelope surfaces the same exc_type/message shape as in-process
            # failures used to. We can't mutate ``__class__.__name__`` on a
            # built-in instance, so we dynamically create a subclass type.
            synth_cls = type(code, (RuntimeError,), {})
            raise _StepFailure("sam", synth_cls(message))

        if terminal_result is None:
            # Listener exited without a terminal — treat as worker stream
            # closure failure so the pipeline doesn't hang.
            msg = "worker stream ended without terminal event"
            await _finalize_run_row(run_id=run_id, status="failed", error=msg)
            raise _StepFailure("sam", RuntimeError(msg))

        await _finalize_run_row(
            run_id=run_id,
            status="completed",
            metrics={
                "images": terminal_result.get("images")
                if isinstance(terminal_result, dict)
                else None,
                "masks_total": terminal_result.get("masks_total")
                if isinstance(terminal_result, dict)
                else None,
                "errors": terminal_result.get("errors")
                if isinstance(terminal_result, dict)
                else None,
            },
        )
        await _mark_step_done(analysis_id=analysis_id, step="sam")
        bridge.step_completed("sam", terminal_result)
        return terminal_result

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
