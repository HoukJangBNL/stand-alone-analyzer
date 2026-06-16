"""SAM dispatch service: defer SAM jobs to GPU worker queue.

Provides the shared `defer_sam_job` function used by both the standalone Run SAM
route and the pipeline's SAM step. Ensures GPU worker availability (cold-start),
emits progress events for UX, and defers the SAM job to procrastinate.

The function accepts either `raw_images_dir` (for local scans) or `s3_prefix` (for
S3-backed scans). The worker determines which sync method to use based on which
parameter is provided.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path


class SupportsGpuLaunching(Protocol):
    """Protocol for progress bridges that support GPU cold-start events."""

    def emit_gpu_launching(self, instance_id: str) -> None:
        """Emit a gpu_launching event when a GPU worker is booting."""
        ...

logger = logging.getLogger(__name__)


async def _ensure_gpu_worker():
    """Launch GPU worker if not running (spot fleet launch, ~60-90s).

    Returns a LaunchResult with action="launched" or action="noop" for
    coordination. The defer proceeds regardless — the worker will drain the
    queue once online.

    Defined as a module-level seam so tests can monkeypatch this symbol
    without requiring a live EC2 fleet.
    """
    from flake_analysis.worker.launcher import (
        PgAdvisoryLock,
        ensure_worker_running,
    )

    return await ensure_worker_running(advisory_lock=PgAdvisoryLock())


async def defer_sam_job(
    *,
    run_id: int,
    analysis_folder: Path | str,
    weights_path: str | None,
    device: str | None,
    raw_images_dir: Path | str | None = None,
    s3_prefix: str | None = None,
    bridge: SupportsGpuLaunching | None = None,
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

    Defined as a shared service function so both the standalone Run SAM route
    and the pipeline's SAM step can use identical deferral logic. Tests can
    monkeypatch this function with a no-op rather than requiring an
    InMemoryConnector or real queue.

    The ``bridge`` parameter is keyword-only and defaults to ``None``
    for backwards compatibility with call sites (or tests) that don't
    care about cold-start UX.

    Args:
        run_id: Run ID for NOTIFY fan-out
        analysis_folder: Analysis folder path for SAM output
        weights_path: Optional custom SAM weights path
        device: Optional device spec (e.g. "cuda:0")
        raw_images_dir: Local path for scans hydrated to disk (for non-S3 scans)
        s3_prefix: S3 prefix for scans in S3 (e.g. "dev/scans/6/")
        bridge: Optional bridge supporting emit_gpu_launching for SSE cold-start events

    Raises:
        RuntimeError: If procrastinate defer fails
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
        "weights_path": str(weights_path) if weights_path else "",
        "device": device,
    }
    if s3_prefix is not None:
        kwargs["s3_prefix"] = s3_prefix
    if raw_images_dir is not None:
        kwargs["raw_images_dir"] = str(raw_images_dir)

    await app.tasks["run_sam"].defer_async(**kwargs)
