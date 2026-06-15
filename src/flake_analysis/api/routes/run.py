"""Compute run endpoints (SSE) per backend design §1.2."""
from __future__ import annotations
import asyncio
import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api import errors as app_errors
from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import (
    get_active_analysis,
    get_db_session,
    get_session_for_background,
)
from flake_analysis.api.mutex import acquire_scan_lock
from flake_analysis.api.services import scans_service
from flake_analysis.api.services.hydrate import ensure_scan_hydrated
from flake_analysis.api.services.runs import record_run_end, record_run_start
from flake_analysis.api.services.s3_presign import PRESIGN_TTL_SECONDS
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
    analysis_folder,
    weights_path,
    device: str | None,
    raw_images_dir=None,
    s3_prefix: str | None = None,
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

    kwargs = {
        "run_id": run_id,
        "analysis_folder": str(analysis_folder),
        "weights_path": str(weights_path),
        "device": device,
    }
    if s3_prefix is not None:
        kwargs["s3_prefix"] = s3_prefix
    if raw_images_dir is not None:
        kwargs["raw_images_dir"] = str(raw_images_dir)

    await app.tasks["run_sam"].defer_async(**kwargs)


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

    For web-uploaded scans (images in S3), this generates a manifest and
    uploads it to S3, then defers with s3_prefix for the worker to sync
    images directly from S3 instead of downloading to the API host.
    """
    import json
    import os
    from flake_analysis.state.paths import analysis_folder as compute_analysis_folder
    from flake_analysis.api.services.sam_manifest import generate_sam_manifest_for_scan

    analysis = await get_active_analysis(scan_id, session)
    if analysis is None:
        raise HTTPException(status_code=404, detail="no analysis for scan")

    # Compute analysis_folder directly without hydrating (no 9GB download)
    root = os.environ.get("SAA_ANALYSIS_ROOT") or os.environ.get(
        "SAA_ANALYSIS_FOLDER", "/mnt/analysis"
    )
    analysis_folder_path = compute_analysis_folder(root, project_id, scan_id)
    analysis_folder_path.mkdir(parents=True, exist_ok=True)

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

    # Generate SAM manifest and upload to S3
    manifest_dict = await generate_sam_manifest_for_scan(session, scan_id=scan_id)
    manifest_json = json.dumps(manifest_dict)

    bucket = os.environ.get("SAA_S3_BUCKET")
    if not bucket:
        raise HTTPException(status_code=500, detail="SAA_S3_BUCKET not configured")

    # Use the real scan_prefix derived from DB images.s3_uri (not hardcoded)
    scan_prefix = manifest_dict.get("scan_prefix", f"scans/{scan_id}/")
    s3_key = f"{scan_prefix}manifest.json"

    # Upload manifest to S3 in executor to avoid blocking event loop
    loop = asyncio.get_running_loop()

    def _upload_manifest():
        import boto3
        s3 = boto3.client("s3")
        s3.put_object(Bucket=bucket, Key=s3_key, Body=manifest_json.encode("utf-8"))

    await loop.run_in_executor(None, _upload_manifest)

    bridge = ProgressBridge()

    async def driver():
        """Defer the SAM job, listen for fan-out, translate to bridge events."""
        try:
            await _defer_sam_job(
                run_id=run_id,
                analysis_folder=analysis_folder_path,
                weights_path=params.weights_path,
                device=params.device,
                s3_prefix=scan_prefix,
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


async def _derive_scan_prefix_from_db(session: AsyncSession, scan_id: int) -> str:
    """Derive the scan's S3 base prefix from its first image's s3_uri.

    Helper for read routes that need to locate S3 assets under the correct
    prefix (which may be empty or "dev/" etc depending on SAA_S3_PREFIX at
    upload time). Returns e.g. "scans/51/" or "dev/scans/6/".

    Raises HTTPException(500) if the scan has no images (can't derive prefix).
    """
    from sqlalchemy import select
    from flake_analysis.db.models import Image
    from flake_analysis.api.services.sam_manifest import derive_scan_s3_prefix

    stmt = select(Image.s3_uri).where(Image.scan_id == scan_id).limit(1)
    result = await session.execute(stmt)
    first_uri = result.scalar_one_or_none()
    if not first_uri:
        raise HTTPException(
            status_code=500,
            detail=f"Cannot derive S3 prefix for scan {scan_id} (no images in DB)"
        )
    return derive_scan_s3_prefix(first_uri)


@router.get("/sam/results")
async def get_sam_results(
    project_id: str,
    scan_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Read SAM per_image_results.json summary from S3 (Task 5).

    Returns the parsed JSON summary containing images count, masks_total,
    errors, and per_image details. The worker (Task 4) uploads this file
    to `s3://{bucket}/{scan_prefix}07_sam/per_image_results.json` after
    a SAM run completes. The scan_prefix is derived from the scan's real
    images.s3_uri to handle non-empty SAA_S3_PREFIX.

    Returns 404 when the file doesn't exist (no SAM run yet or still running).
    Auth: same project-level access as other scan routes.
    """
    # Verify user has access to this scan
    await scans_service.get_scan_for_user(session, scan_id=scan_id, user=user)

    bucket = os.environ.get("SAA_S3_BUCKET")
    if not bucket:
        logger.error(
            "get_sam_results aborted: SAA_S3_BUCKET not configured",
            extra={"scan_id": scan_id},
        )
        raise app_errors.S3NotConfigured(scan_id=scan_id)

    # Derive the real scan prefix from DB (not hardcoded)
    scan_prefix = await _derive_scan_prefix_from_db(session, scan_id)
    key = f"{scan_prefix}07_sam/per_image_results.json"

    # Read from S3 in executor (sync boto3)
    def _get_s3_object() -> bytes:
        client = boto3.client("s3", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-2"))
        try:
            resp = client.get_object(Bucket=bucket, Key=key)
            return resp["Body"].read()
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                raise HTTPException(
                    status_code=404,
                    detail=f"No SAM results for scan {scan_id} (file not found in S3)",
                ) from exc
            logger.exception(
                "get_sam_results S3 error",
                extra={"scan_id": scan_id, "s3_key": key, "s3_error_code": code},
            )
            raise HTTPException(
                status_code=500,
                detail=f"S3 error reading SAM results: {code}",
            ) from exc

    loop = asyncio.get_running_loop()
    body_bytes = await loop.run_in_executor(None, _get_s3_object)

    # Parse and return JSON
    try:
        results = json.loads(body_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.exception(
            "get_sam_results: invalid JSON in S3 object",
            extra={"scan_id": scan_id, "s3_key": key},
        )
        raise HTTPException(
            status_code=500,
            detail="SAM results file is corrupted (invalid JSON)",
        ) from exc

    return results


@router.get("/sam/masks")
async def get_sam_masks(
    project_id: str,
    scan_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """List SAM mask objects under {scan_prefix}07_sam/ with presigned GET URLs (Task 5).

    Returns a dict with a `masks` key containing a list of objects, each with:
    - `key`: the S3 object key
    - `url`: a presigned GET URL (short TTL) for the browser to fetch directly

    The scan_prefix is derived from the scan's real images.s3_uri to handle
    non-empty SAA_S3_PREFIX (e.g. dev/scans/6/).

    Returns an empty list when no SAM run has occurred yet.
    Auth: same project-level access as other scan routes.
    """
    # Verify user has access to this scan
    await scans_service.get_scan_for_user(session, scan_id=scan_id, user=user)

    bucket = os.environ.get("SAA_S3_BUCKET")
    if not bucket:
        logger.error(
            "get_sam_masks aborted: SAA_S3_BUCKET not configured",
            extra={"scan_id": scan_id},
        )
        raise app_errors.S3NotConfigured(scan_id=scan_id)

    # Derive the real scan prefix from DB (not hardcoded)
    scan_prefix = await _derive_scan_prefix_from_db(session, scan_id)
    prefix = f"{scan_prefix}07_sam/"

    # List S3 objects and presign in executor (sync boto3)
    def _list_and_presign() -> list[dict[str, str]]:
        client = boto3.client("s3", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-2"))
        try:
            resp = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            logger.exception(
                "get_sam_masks S3 list error",
                extra={"scan_id": scan_id, "prefix": prefix, "s3_error_code": code},
            )
            raise HTTPException(
                status_code=500,
                detail=f"S3 error listing masks: {code}",
            ) from exc

        objects = resp.get("Contents", [])
        if not objects:
            return []

        # Presign each object
        masks = []
        for obj in objects:
            key = obj["Key"]
            url = client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=PRESIGN_TTL_SECONDS,
                HttpMethod="GET",
            )
            masks.append({"key": key, "url": url})
        return masks

    loop = asyncio.get_running_loop()
    masks = await loop.run_in_executor(None, _list_and_presign)

    return {"masks": masks}
