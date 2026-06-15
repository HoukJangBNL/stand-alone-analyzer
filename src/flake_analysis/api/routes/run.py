"""Compute run endpoints (SSE) per backend design §1.2."""
from __future__ import annotations
import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import (
    get_active_analysis,
    get_db_session,
    get_session_for_background,
)
from flake_analysis.api.mutex import acquire_scan_lock
from flake_analysis.api.services.hydrate import ensure_scan_hydrated
from flake_analysis.api.services.runs import record_run_end, record_run_start
from flake_analysis.api.sse import ProgressBridge, sse_stream
from flake_analysis.api.schemas.compute import (
    BackgroundParams,
    DomainProximityParams,
    DomainStatsParams,
    SamParams,
    ThumbnailsParams,
)
from flake_analysis.api.sse_listen import listen_for_run
from flake_analysis.pipeline.background import run_background_step
from flake_analysis.pipeline.domain_proximity import run_domain_proximity_step
from flake_analysis.pipeline.domain_stats import run_domain_stats_step
from flake_analysis.pipeline.thumbnails import run_thumbnails_step

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/projects/{project_id}/scans/{scan_id}/run", tags=["run"]
)

@router.post("/thumbnails")
async def run_thumbnails(
    project_id: str,
    scan_id: int,
    params: ThumbnailsParams,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Run thumbnails step with SSE progress."""
    manifest = await ensure_scan_hydrated(
        session, project_id=project_id, scan_id=scan_id
    )

    # Acquire the per-scan lock synchronously so a contended request gets an
    # HTTP-level error envelope (ProjectBusy -> 423) instead of an SSE stream
    # that opens and immediately errors. The lock must be held for the lifetime
    # of the generator, so we enter the context manager manually here and exit
    # it in the generator's finally block.
    lock_cm = acquire_scan_lock(scan_id)
    await lock_cm.__aenter__()

    # Emit usage event BEFORE starting the SSE stream
    from flake_analysis.api.services.usage import emit

    await emit(
        session,
        user,
        "scan_run",
        {"step": "thumbnails", "project_id": project_id, "scan_id": scan_id},
    )
    await session.commit()

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
            async for frame in sse_stream(bridge):
                yield frame
        finally:
            try:
                await task
            finally:
                await lock_cm.__aexit__(None, None, None)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/background")
async def run_background(
    project_id: str,
    scan_id: int,
    params: BackgroundParams,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Run background generation step with SSE progress."""
    manifest = await ensure_scan_hydrated(
        session, project_id=project_id, scan_id=scan_id
    )

    analysis = await get_active_analysis(scan_id, session)
    if analysis is None:
        raise HTTPException(status_code=404, detail="no analysis for scan")

    # Acquire the per-scan lock synchronously so a contended request gets an
    # HTTP-level error envelope (ProjectBusy -> 423) instead of an SSE stream
    # that opens and immediately errors. The lock must be held for the lifetime
    # of the generator, so we enter the context manager manually here and exit
    # it in the generator's finally block.
    lock_cm = acquire_scan_lock(scan_id)
    await lock_cm.__aenter__()

    # Emit usage event BEFORE starting the SSE stream
    from flake_analysis.api.services.usage import emit

    await emit(
        session,
        user,
        "scan_run",
        {"step": "background", "project_id": project_id, "scan_id": scan_id},
    )
    await session.commit()

    run_id = await record_run_start(
        session, analysis_id=analysis.id, step="background"
    )
    await session.commit()

    bridge = ProgressBridge()

    def call_wrapper():
        return run_background_step(
            raw_images_dir=manifest.raw_images_dir,
            analysis_folder=manifest.analysis_folder,
            seed=params.seed,
            max_images=params.max_images,
            gaussian_sigma=params.gaussian_sigma,
            method=params.method,
            progress_callback=bridge.emit_progress,
        )

    async def generate():
        loop = asyncio.get_running_loop()

        async def run_pipeline():
            try:
                result = await loop.run_in_executor(None, call_wrapper)
                async with get_session_for_background() as bg:
                    await record_run_end(
                        bg,
                        run_id=run_id,
                        status="completed",
                        metrics={
                            "max_images": params.max_images,
                            "method": params.method,
                        },
                    )
                    await bg.commit()
                bridge.emit_done(result)
            except Exception as e:
                async with get_session_for_background() as bg:
                    await record_run_end(
                        bg, run_id=run_id, status="failed", error=str(e)
                    )
                    await bg.commit()
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


@router.post("/domain_stats")
async def run_domain_stats(
    project_id: str,
    scan_id: int,
    params: DomainStatsParams,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Run domain stats step with SSE progress."""
    manifest = await ensure_scan_hydrated(
        session, project_id=project_id, scan_id=scan_id
    )

    analysis = await get_active_analysis(scan_id, session)
    if analysis is None:
        raise HTTPException(status_code=404, detail="no analysis for scan")

    # Acquire the per-scan lock synchronously so a contended request gets an
    # HTTP-level error envelope (ProjectBusy -> 423) instead of an SSE stream
    # that opens and immediately errors. The lock must be held for the lifetime
    # of the generator, so we enter the context manager manually here and exit
    # it in the generator's finally block.
    lock_cm = acquire_scan_lock(scan_id)
    await lock_cm.__aenter__()

    # Emit usage event BEFORE starting the SSE stream
    from flake_analysis.api.services.usage import emit

    await emit(
        session,
        user,
        "scan_run",
        {"step": "domain_stats", "project_id": project_id, "scan_id": scan_id},
    )
    await session.commit()

    run_id = await record_run_start(
        session, analysis_id=analysis.id, step="domain_stats"
    )
    await session.commit()

    bridge = ProgressBridge()

    def call_wrapper():
        return run_domain_stats_step(
            raw_images_dir=manifest.raw_images_dir,
            annotations_path=manifest.annotations_path,
            analysis_folder=manifest.analysis_folder,
            repr_mode=params.repr_mode,
            raw_ext=params.raw_ext,
            progress_callback=bridge.emit_progress,
        )

    async def generate():
        loop = asyncio.get_running_loop()

        async def run_pipeline():
            try:
                result = await loop.run_in_executor(None, call_wrapper)
                async with get_session_for_background() as bg:
                    await record_run_end(
                        bg,
                        run_id=run_id,
                        status="completed",
                        metrics={
                            "repr_mode": params.repr_mode,
                            "raw_ext": params.raw_ext,
                        },
                    )
                    await bg.commit()
                bridge.emit_done(result)
            except Exception as e:
                async with get_session_for_background() as bg:
                    await record_run_end(
                        bg, run_id=run_id, status="failed", error=str(e)
                    )
                    await bg.commit()
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


async def _ensure_gpu_worker():
    """Boot a GPU worker EC2 instance if none is live (P4.4).

    Returns the :class:`flake_analysis.worker.launcher.LaunchResult`
    so the caller (e.g. :func:`_defer_sam_job`) can decide whether to
    emit a ``gpu_launching`` SSE frame. ``action == "launched"``
    means we just kicked off a fresh spot boot and ``instance_id`` is
    populated; ``action == "noop"`` means a worker was already live.

    Module-level seam so tests can monkeypatch with a no-op or a
    canned ``LaunchResult``. The production implementation calls
    ``ensure_worker_running`` from
    :mod:`flake_analysis.worker.launcher`, which checks the EC2 fleet
    and (optionally) launches a single spot instance via the
    ``qpress-sam-gpu-worker`` launch template.
    """
    from flake_analysis.worker.launcher import (
        PgAdvisoryLock,
        ensure_worker_running,
    )

    return await ensure_worker_running(advisory_lock=PgAdvisoryLock())


async def _defer_sam_job(
    *,
    run_id: int,
    raw_images_dir,
    analysis_folder,
    weights_path,
    device: str | None,
    bridge: ProgressBridge | None = None,
) -> None:
    """Push a SAM job onto the procrastinate ``gpu`` queue.

    Before deferring, ensures a GPU worker exists (P4.4). If the fleet
    is empty, this kicks off a spot launch via the
    ``qpress-sam-gpu-worker`` launch template and — when a ``bridge``
    is supplied — emits a non-terminal ``gpu_launching`` SSE frame so
    the frontend can render the cold-start wait (~60-90s spot
    allocation + boot). When a worker is already live (``action ==
    "noop"``), no frame is emitted and the defer proceeds immediately.

    The defer itself does not wait for the worker to come online — the
    SSE stream stays open and the worker drains the procrastinate
    queue once it boots (3-5 min cold start total).

    Defined as a module-level seam so tests can monkeypatch this symbol
    with a no-op rather than requiring an InMemoryConnector or real
    queue. The real implementation imports the production app lazily so
    test files that only patch ``_stream_sam_events`` don't pay the
    psycopg-pool open cost.

    The ``bridge`` parameter is keyword-only and defaults to ``None``
    for backwards compatibility with call sites (or tests) that don't
    care about cold-start UX.
    """
    launch_result = await _ensure_gpu_worker()

    # Emit gpu_launching ONLY when we know we just kicked off a fresh
    # boot and have an instance_id to report. Defensive try/except —
    # an SSE emit failure must never cancel the actual defer.
    if (
        bridge is not None
        and launch_result is not None
        and getattr(launch_result, "action", None) == "launched"
        and getattr(launch_result, "instance_id", None) is not None
    ):
        try:
            bridge.emit_gpu_launching(launch_result.instance_id)
        except Exception:  # noqa: BLE001 — never let SSE emit failures cancel defer
            logger.exception(
                "gpu_launching emit failed for run_id=%s", run_id,
            )

    # Importing the tasks module registers @app.task decorators on the
    # production App. The connector pool is opened lazily by procrastinate
    # the first time defer_async runs.
    from flake_analysis.worker import tasks as _tasks  # noqa: F401
    from flake_analysis.worker.app import app

    await app.tasks["run_sam"].defer_async(
        run_id=run_id,
        raw_images_dir=str(raw_images_dir),
        analysis_folder=str(analysis_folder),
        weights_path=str(weights_path),
        device=device,
    )


def _stream_sam_events(run_id: int):
    """Yield decoded NOTIFY payloads from the worker's progress channel.

    Module-level seam: tests patch this with a fake async iterator that
    emits the canned ``progress``/``completed``/``error`` payloads they
    want to assert on, without needing a live LISTEN/NOTIFY connection.
    """
    return listen_for_run(run_id)


@router.post("/sam")
async def run_sam(
    project_id: str,
    scan_id: int,
    params: SamParams,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Run SAM2 inference via the procrastinate ``gpu`` worker queue (P4.2).

    Flow: acquire scan lock → emit usage → record_run_start → defer the
    job → LISTEN on ``sam_progress:{run_id}`` → translate each payload
    into the existing single-step SSE wire format (``progress`` / ``done``
    / ``error``). Wire format is byte-identical to the in-process
    predecessor so the frontend doesn't notice the swap.

    The route never invokes ``flake_analysis.pipeline.sam.run_sam_step``
    directly anymore — that runner now lives inside
    :func:`flake_analysis.worker.tasks.run_sam`, which a GPU-resident
    worker process drains. Failures inside the worker arrive as
    ``error`` notifications and are translated to the same
    ``pipeline_failed`` envelope shape.
    """
    manifest = await ensure_scan_hydrated(
        session, project_id=project_id, scan_id=scan_id
    )

    analysis = await get_active_analysis(scan_id, session)
    if analysis is None:
        raise HTTPException(status_code=404, detail="no analysis for scan")

    lock_cm = acquire_scan_lock(scan_id)
    await lock_cm.__aenter__()

    # Emit usage event BEFORE starting the SSE stream
    from flake_analysis.api.services.usage import emit

    await emit(
        session,
        user,
        "scan_run",
        {"step": "sam", "project_id": project_id, "scan_id": scan_id},
    )
    await session.commit()

    run_id = await record_run_start(session, analysis_id=analysis.id, step="sam")
    await session.commit()

    bridge = ProgressBridge()

    async def driver():
        """Defer the SAM job, listen for fan-out, translate to bridge events."""
        try:
            await _defer_sam_job(
                run_id=run_id,
                raw_images_dir=manifest.raw_images_dir,
                analysis_folder=manifest.analysis_folder,
                weights_path=params.weights_path,
                device=params.device,
                bridge=bridge,
            )

            terminal_seen = False
            async for payload in _stream_sam_events(run_id):
                ptype = payload.get("type")
                if ptype == "progress":
                    bridge.emit_progress(
                        float(payload.get("progress", 0.0)),
                        str(payload.get("message", "")),
                    )
                elif ptype == "gpu_ready":
                    # Non-terminal: worker just picked up the job and is
                    # about to load the SAM model. Frontend flips from
                    # "Launching..." to "GPU ready, processing N images".
                    bridge.emit_gpu_ready(
                        int(payload.get("image_count", 0) or 0)
                    )
                elif ptype == "completed":
                    result = payload.get("result", {}) or {}
                    async with get_session_for_background() as bg:
                        await record_run_end(
                            bg,
                            run_id=run_id,
                            status="completed",
                            metrics={
                                "images": result.get("images"),
                                "masks_total": result.get("masks_total"),
                                "errors": result.get("errors"),
                            },
                        )
                        await bg.commit()
                    bridge.emit_done(result)
                    terminal_seen = True
                    break
                elif ptype == "error":
                    code = str(payload.get("code") or "pipeline_failed")
                    message = str(payload.get("message") or "")
                    async with get_session_for_background() as bg:
                        await record_run_end(
                            bg, run_id=run_id, status="failed", error=message
                        )
                        await bg.commit()
                    bridge.emit_error(
                        "pipeline_failed", message, {"exc_type": code}
                    )
                    terminal_seen = True
                    break

            if not terminal_seen:
                # Listener exited without a terminal — treat as failure so the
                # client doesn't hang and the runs row reflects the truth.
                async with get_session_for_background() as bg:
                    await record_run_end(
                        bg,
                        run_id=run_id,
                        status="failed",
                        error="worker stream ended without terminal event",
                    )
                    await bg.commit()
                bridge.emit_error(
                    "pipeline_failed",
                    "worker stream ended without terminal event",
                    {"exc_type": "WorkerStreamClosed"},
                )
        except BaseException as e:  # noqa: BLE001
            # Defer-side or listener-setup failures land here.
            async with get_session_for_background() as bg:
                await record_run_end(bg, run_id=run_id, status="failed", error=str(e))
                await bg.commit()
            bridge.emit_error(
                "pipeline_failed", str(e), {"exc_type": type(e).__name__}
            )
        finally:
            bridge.close()

    async def generate():
        task = asyncio.create_task(driver())
        try:
            async for frame in sse_stream(bridge):
                yield frame
        finally:
            try:
                await task
            finally:
                await lock_cm.__aexit__(None, None, None)

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/domain_proximity")
async def run_domain_proximity(
    project_id: str,
    scan_id: int,
    params: DomainProximityParams,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Run domain proximity step with SSE progress."""
    manifest = await ensure_scan_hydrated(
        session, project_id=project_id, scan_id=scan_id
    )

    analysis = await get_active_analysis(scan_id, session)
    if analysis is None:
        raise HTTPException(status_code=404, detail="no analysis for scan")

    # Acquire the per-scan lock synchronously so a contended request gets an
    # HTTP-level error envelope (ProjectBusy -> 423) instead of an SSE stream
    # that opens and immediately errors. The lock must be held for the lifetime
    # of the generator, so we enter the context manager manually here and exit
    # it in the generator's finally block.
    lock_cm = acquire_scan_lock(scan_id)
    await lock_cm.__aenter__()

    # Emit usage event BEFORE starting the SSE stream
    from flake_analysis.api.services.usage import emit

    await emit(
        session,
        user,
        "scan_run",
        {"step": "domain_proximity", "project_id": project_id, "scan_id": scan_id},
    )
    await session.commit()

    run_id = await record_run_start(
        session, analysis_id=analysis.id, step="domain_proximity"
    )
    await session.commit()

    bridge = ProgressBridge()

    def call_wrapper():
        return run_domain_proximity_step(
            annotations_path=manifest.annotations_path,
            analysis_folder=manifest.analysis_folder,
            r_max_px=params.r_max_px,
            min_area_px=params.min_area_px,
            max_area_px=params.max_area_px,
            d_touch_px=params.d_touch_px,
            pixel_size_um=params.pixel_size_um,
            link_distance_um=params.link_distance_um,
            workers=params.workers,
            progress_callback=bridge.emit_progress,
        )

    async def generate():
        loop = asyncio.get_running_loop()

        async def run_pipeline():
            try:
                result = await loop.run_in_executor(None, call_wrapper)
                async with get_session_for_background() as bg:
                    await record_run_end(
                        bg,
                        run_id=run_id,
                        status="completed",
                        metrics={
                            "r_max_px": params.r_max_px,
                            "workers": params.workers,
                        },
                    )
                    await bg.commit()
                bridge.emit_done(result)
            except Exception as e:
                async with get_session_for_background() as bg:
                    await record_run_end(
                        bg, run_id=run_id, status="failed", error=str(e)
                    )
                    await bg.commit()
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
