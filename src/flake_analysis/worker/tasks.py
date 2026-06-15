"""Worker task definitions (P4.2.c).

The single task here, :func:`run_sam`, wraps the in-process SAM step
runner so it can be deferred to a procrastinate queue. The API process
no longer calls :func:`flake_analysis.pipeline.sam.run_sam_step`
directly — it defers a job, and a GPU-resident worker picks it up
via :data:`flake_analysis.worker.app.app`.

Progress fan-out
----------------
Pipeline steps emit ``(progress: float, message: str)`` samples through
a ``progress_callback`` parameter. We bridge those to the API process
via PostgreSQL ``NOTIFY`` on a per-run channel (``sam_progress:{run_id}``).
The API's SSE endpoint LISTENs on that channel and relays each
notification back to the browser as a ``progress`` SSE frame.

The actual emit function (:func:`_emit_progress`) is module-level so
tests can monkeypatch it with a list collector — see
``tests/worker/test_tasks.py``. In production it serializes the payload
as JSON and runs ``NOTIFY`` through a sync psycopg connection.

Wire format
-----------
Each emit takes::

    {
        "type": "progress" | "completed" | "error",
        ... type-specific fields ...
    }

- ``progress``: ``{"progress": float, "message": str}``
- ``completed``: ``{"result": dict}`` (forwarded from the runner)
- ``error``: ``{"code": str, "message": str}`` (exception class + str)

The SSE relay re-shapes these into the existing 5-event vocabulary
(``step_started`` / ``step_progress`` / ``step_completed`` /
``pipeline_done`` / ``pipeline_error``) so the frontend wire format
stays byte-identical.
"""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import boto3
import psycopg

from flake_analysis.db.url import DbSettings, _require_ssl
from flake_analysis.pipeline.sam import run_sam_step
from flake_analysis.worker.app import app
from flake_analysis.worker.markers import emit_marker

logger = logging.getLogger(__name__)

# Image extensions matched by core/pipeline/sam.py::_list_images (the
# `exts` local at sam.py:136). Mirrored here so the gpu_ready preview
# count agrees with what the SAM step actually iterates. If sam.py's
# whitelist changes, update this set in lockstep.
_IMG_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


def _channel_name(run_id: int) -> str:
    """Per-run NOTIFY channel."""
    return f"sam_progress:{run_id}"


def _emit_progress(*, run_id: int, payload: dict[str, Any]) -> None:
    """Emit a progress payload to the API via PG NOTIFY.

    Default implementation opens a short-lived psycopg connection and
    issues ``NOTIFY <channel>, <json>``. Tests monkeypatch this symbol
    with a list collector so no DB is needed.

    Channel name is the same value the API's LISTEN-side helper computes
    from ``run_id`` — see :mod:`flake_analysis.api.sse_listen`.
    """
    s = DbSettings()
    conn_kwargs: dict[str, Any] = {
        "host": s.db_host,
        "port": s.db_port,
        "dbname": s.db_name,
    }
    if _require_ssl(s.db_host):
        # RDS rds.force_ssl=1: SSL-only, no prefer→fallback. See #217.
        conn_kwargs["sslmode"] = "require"
    if s.db_user:
        conn_kwargs["user"] = s.db_user
    if s.db_password:
        conn_kwargs["password"] = s.db_password

    channel = _channel_name(run_id)
    body = json.dumps(payload, default=str)
    # autocommit so the NOTIFY is delivered immediately (NOTIFY is
    # transactional — without commit it would queue until the
    # transaction completes).
    with psycopg.connect(**conn_kwargs, autocommit=True) as conn:
        with conn.cursor() as cur:
            # psycopg adapts the channel name to a literal identifier;
            # using parameterized NOTIFY via pg_notify() keeps it safe
            # even if a malicious value reached here.
            cur.execute("SELECT pg_notify(%s, %s)", (channel, body))


def _sync_scan_from_s3(
    s3_prefix: str,
    dest_dir: Path,
    *,
    bucket: str,
) -> int:
    """Sync images from S3 for a web-uploaded scan.

    Downloads manifest.json from {s3_prefix}manifest.json, then syncs each
    image from the REAL S3 key (from manifest entry["key"]) to dest_dir/{filename}.
    The manifest carries the actual s3_uri-derived key per image, so this works
    for any SAA_S3_PREFIX (empty or "dev/" etc). Returns the count of images synced.

    Uses bounded concurrency (ThreadPoolExecutor with ~12 workers) since a
    full scan has up to 3648 files. Skips files that already exist locally
    with nonzero size (idempotent). Raises if downloaded count < manifest
    count (don't run SAM on a partial set).

    Args:
        s3_prefix: S3 prefix for the scan (e.g. "scans/42/" or "dev/scans/6/")
        dest_dir: Local directory to sync images to
        bucket: S3 bucket name

    Returns:
        Number of images synced

    Raises:
        RuntimeError: If sync fails or downloaded count doesn't match manifest
    """
    s3 = boto3.client("s3")
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Download manifest
    manifest_key = f"{s3_prefix}manifest.json"
    try:
        response = s3.get_object(Bucket=bucket, Key=manifest_key)
        manifest = json.loads(response["Body"].read())
    except Exception as exc:
        msg = f"Failed to download manifest from s3://{bucket}/{manifest_key}"
        logger.exception(msg)
        raise RuntimeError(msg) from exc

    images = manifest.get("images", [])
    if not images:
        logger.warning("Manifest has no images, nothing to sync")
        return 0

    def _download_one(entry: dict) -> bool:
        """Download one image, return True if actually downloaded."""
        # Use the real S3 key from the manifest (derived from DB s3_uri)
        s3_key = entry.get("key")
        if not s3_key:
            # Fallback for old manifests without key field (reconstructed path)
            sha = entry["sha256"]
            s3_key = f"{s3_prefix}images/{sha}.png"
            logger.warning("Manifest entry missing 'key', falling back to reconstructed path: %s", s3_key)

        filename = entry.get("filename") or entry["sha256"] + ".png"
        local_path = dest_dir / filename

        # Skip if already exists with nonzero size (idempotent)
        if local_path.exists() and local_path.stat().st_size > 0:
            return False

        try:
            s3.download_file(bucket, s3_key, str(local_path))
            return True
        except Exception:
            logger.exception("Failed to download s3://%s/%s", bucket, s3_key)
            raise

    # Sync with bounded concurrency (~12 workers for up to 3648 files)
    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(_download_one, images))

    downloaded = sum(results)
    total_expected = len(images)

    if downloaded + sum(1 for r in results if not r) < total_expected:
        msg = f"Incomplete sync: expected {total_expected} images, got {downloaded} new + {sum(1 for r in results if not r)} existing"
        logger.error(msg)
        raise RuntimeError(msg)

    logger.info("Synced %d images from s3://%s/%s", len(images), bucket, s3_prefix)
    return len(images)


def _upload_results_to_s3(
    local_07sam_dir: Path,
    s3_prefix: str,
    *,
    bucket: str,
) -> None:
    """Upload SAM results from local 07_sam/ directory to S3.

    Walks the local directory and uploads each file to
    {s3_prefix}07_sam/{relpath}.

    Args:
        local_07sam_dir: Local 07_sam directory with SAM outputs
        s3_prefix: S3 prefix for the scan (e.g. "scans/42/")
        bucket: S3 bucket name
    """
    if not local_07sam_dir.exists():
        logger.warning("07_sam directory does not exist, nothing to upload")
        return

    s3 = boto3.client("s3")
    uploaded = 0

    for local_file in local_07sam_dir.rglob("*"):
        if not local_file.is_file():
            continue

        relpath = local_file.relative_to(local_07sam_dir)
        s3_key = f"{s3_prefix}07_sam/{relpath}"

        try:
            s3.upload_file(str(local_file), bucket, s3_key)
            uploaded += 1
        except Exception:
            logger.exception("Failed to upload %s to s3://%s/%s", local_file, bucket, s3_key)
            raise

    logger.info("Uploaded %d SAM result files to s3://%s/%s07_sam/", uploaded, bucket, s3_prefix)


@app.task(queue="gpu", name="run_sam")
def run_sam(
    *,
    run_id: int,
    raw_images_dir: str | None = None,
    s3_prefix: str | None = None,
    analysis_folder: str,
    weights_path: str | None = None,
    device: str | None = None,
    model_meta: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run SAM2 inference, fan-out progress + markers, return runner result.

    Two execution paths:
    1. **S3 path** (web-uploaded scans): When ``s3_prefix`` is given, downloads
       manifest.json from S3, syncs images to a local per-run directory (renaming
       sha256 keys to original ix/iy filenames for grid parsing), runs SAM on
       that directory, then uploads the 07_sam/ results back to S3.
    2. **Local path** (measure-run): When ``raw_images_dir`` is given and
       ``s3_prefix`` is None, runs SAM directly on the local directory with no
       S3 sync or upload (backward-compatible).

    Marker fan-out: progress messages whose text starts with ``"marker:"``
    are routed to :func:`emit_marker` (worker_events sink) instead of
    SSE NOTIFY. All other progress messages flow through the existing
    SSE path unchanged.

    Lifecycle: emits ``sam_task_start`` at entry (with ``model_meta`` and
    inputs in the payload) and ``sam_task_end`` at exit (with
    ``status``, ``masks_total``, ``errors``, and ``exc`` on failure).
    These let offline analysis derive total wall time without joining
    against ``procrastinate_jobs``.
    """
    # S3 path: sync images from S3 to a local per-run directory
    bucket = os.environ.get("SAA_S3_BUCKET", "qpress-uploads")
    run_base = os.environ.get("SAM_RUN_BASE", "/opt/sam/runs")
    if s3_prefix:
        local_raw = Path(run_base) / str(run_id) / "raw_images"
        try:
            _emit_progress(
                run_id=run_id,
                payload={
                    "type": "progress",
                    "progress": 0.05,
                    "message": "Syncing images from storage…",
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("sync progress emit failed for run_id=%s", run_id)

        try:
            n_imgs = _sync_scan_from_s3(s3_prefix, local_raw, bucket=bucket)
        except Exception as exc:
            try:
                _emit_progress(
                    run_id=run_id,
                    payload={
                        "type": "error",
                        "code": type(exc).__name__,
                        "message": f"Failed to sync images from S3: {exc}",
                    },
                )
            except Exception:  # noqa: BLE001
                logger.exception("error emit failed for run_id=%s", run_id)
            raise

        effective_raw_images_dir = str(local_raw)
    else:
        # Measure-run path: use the passed raw_images_dir directly
        if not raw_images_dir:
            raise ValueError("Either s3_prefix or raw_images_dir must be provided")
        effective_raw_images_dir = raw_images_dir
        # Count images for gpu_ready
        try:
            n_imgs = sum(
                1
                for p in Path(raw_images_dir).iterdir()
                if p.is_file() and p.suffix.lower() in _IMG_SUFFIXES
            )
        except Exception:  # noqa: BLE001 — count failure must not block the run
            n_imgs = 0

    # Announce the cold-start handoff to the SSE consumer. The frontend
    # flips from 'Launching GPU…' to 'GPU ready, processing N images'
    # on this event. This must fire AFTER the S3 sync (if applicable) so
    # we have an accurate image count, but BEFORE sam_task_start.
    try:
        _emit_progress(
            run_id=run_id,
            payload={"type": "gpu_ready", "image_count": int(n_imgs)},
        )
    except Exception:  # noqa: BLE001 — never let SSE emit failures cancel the job
        logger.exception("gpu_ready emit failed for run_id=%s", run_id)

    emit_marker(
        run_id=run_id,
        event="sam_task_start",
        payload={
            "raw_images_dir": effective_raw_images_dir,
            "s3_prefix": s3_prefix,
            "analysis_folder": analysis_folder,
            "weights_path": weights_path,
            "model_meta": model_meta,
        },
    )

    def _on_progress(progress: float, message: str) -> None:
        msg = str(message)
        if msg.startswith("marker:"):
            try:
                emit_marker(run_id=run_id, event=msg, payload=None)
            except Exception:  # noqa: BLE001 — never let marker emit failures
                logger.exception("marker emit failed for run_id=%s", run_id)
            return
        try:
            _emit_progress(
                run_id=run_id,
                payload={
                    "type": "progress",
                    "progress": float(progress),
                    "message": msg,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("progress emit failed for run_id=%s", run_id)

    status = "success"
    masks_total = 0
    errors = 0
    try:
        # Multi-GPU path ignores weights_path (AMI-baked M3); single-GPU needs it.
        # Empty/None is fine for prod path; local dev fallback would need real path.
        result = run_sam_step(
            raw_images_dir=effective_raw_images_dir,
            analysis_folder=analysis_folder,
            weights_path=weights_path or "",
            device=device,
            progress_callback=_on_progress,
        )
        masks_total = int(result.get("masks_total", 0) or 0)
        errors = int(result.get("errors", 0) or 0)

        # S3 path: upload results back to S3
        if s3_prefix:
            try:
                _emit_progress(
                    run_id=run_id,
                    payload={
                        "type": "progress",
                        "progress": 0.95,
                        "message": "Uploading results to storage…",
                    },
                )
            except Exception:  # noqa: BLE001
                logger.exception("upload progress emit failed for run_id=%s", run_id)

            try:
                local_07sam = Path(analysis_folder) / "07_sam"
                _upload_results_to_s3(local_07sam, s3_prefix, bucket=bucket)
            except Exception as exc:
                try:
                    _emit_progress(
                        run_id=run_id,
                        payload={
                            "type": "error",
                            "code": type(exc).__name__,
                            "message": f"Failed to upload results to S3: {exc}",
                        },
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("error emit failed for run_id=%s", run_id)
                raise

    except BaseException as exc:  # noqa: BLE001 — re-raised below
        status = "failed"
        try:
            _emit_progress(
                run_id=run_id,
                payload={
                    "type": "error",
                    "code": type(exc).__name__,
                    "message": str(exc),
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception("error emit failed for run_id=%s", run_id)
        emit_marker(
            run_id=run_id,
            event="sam_task_end",
            payload={
                "status": status,
                "masks_total": masks_total,
                "errors": errors,
                "exc": type(exc).__name__,
            },
        )
        raise

    try:
        # T7p (§42 fix): Postgres NOTIFY has an 8000-byte payload limit.
        # `result["per_image"]` is a 100+ entry dict (~10 KB+ JSON) and
        # blows the cap, so the entire `completed` notify is rejected
        # with InvalidParameterValue and the SSE consumer never gets
        # `done`. Send only the summary scalars over NOTIFY; per_image
        # detail stays on disk (per_image_results.json) for any
        # consumer that needs it.
        slim_result = {
            "images": int(result.get("images", 0) or 0),
            "masks_total": int(result.get("masks_total", 0) or 0),
            "errors": int(result.get("errors", 0) or 0),
        }
        _emit_progress(
            run_id=run_id,
            payload={"type": "completed", "result": slim_result},
        )
    except Exception:  # noqa: BLE001
        logger.exception("completed emit failed for run_id=%s", run_id)

    emit_marker(
        run_id=run_id,
        event="sam_task_end",
        payload={
            "status": status,
            "masks_total": masks_total,
            "errors": errors,
        },
    )
    return result
