"""Compute run endpoints (SSE) per backend design §1.2."""
from __future__ import annotations
import asyncio
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import (
    get_active_analysis,
    get_db_session,
    get_manifest,
    get_session_for_background,
)
from flake_analysis.api.mutex import acquire_scan_lock
from flake_analysis.api.services.runs import record_run_end, record_run_start
from flake_analysis.api.sse import ProgressBridge, sse_stream
from flake_analysis.api.schemas.compute import (
    BackgroundParams,
    DomainProximityParams,
    DomainStatsParams,
    SamParams,
    ThumbnailsParams,
)
from flake_analysis.pipeline.background import run_background_step
from flake_analysis.pipeline.domain_proximity import run_domain_proximity_step
from flake_analysis.pipeline.domain_stats import run_domain_stats_step
from flake_analysis.pipeline.sam import run_sam_step
from flake_analysis.pipeline.thumbnails import run_thumbnails_step

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
    manifest = await get_manifest(project_id=project_id, scan_id=scan_id)

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
    manifest = await get_manifest(project_id=project_id, scan_id=scan_id)

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
    manifest = await get_manifest(project_id=project_id, scan_id=scan_id)

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


@router.post("/sam")
async def run_sam(
    project_id: str,
    scan_id: int,
    params: SamParams,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Run SAM2 inference step with SSE progress."""
    manifest = await get_manifest(project_id=project_id, scan_id=scan_id)

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
        {"step": "sam", "project_id": project_id, "scan_id": scan_id},
    )
    await session.commit()

    run_id = await record_run_start(
        session, analysis_id=analysis.id, step="sam"
    )
    await session.commit()

    bridge = ProgressBridge()

    def call_wrapper():
        return run_sam_step(
            raw_images_dir=manifest.raw_images_dir,
            analysis_folder=manifest.analysis_folder,
            weights_path=params.weights_path,
            device=params.device,
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
                            "images": result.get("images"),
                            "masks_total": result.get("masks_total"),
                            "errors": result.get("errors"),
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


@router.post("/domain_proximity")
async def run_domain_proximity(
    project_id: str,
    scan_id: int,
    params: DomainProximityParams,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Run domain proximity step with SSE progress."""
    manifest = await get_manifest(project_id=project_id, scan_id=scan_id)

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
