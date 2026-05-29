#!/usr/bin/env python3
"""One-shot defer launcher executed via SSM on the GPU worker instance.

Loads worker env from /etc/flake-analysis-worker.env, resolves the
model URI to a local artifact + metadata, defers run_sam to the
procrastinate gpu queue, prints job_id, exits.

Re-uses prod-grade unit methods from flake_analysis.worker.measurement.
DO NOT add measurement-specific logic here — this script is intentionally
thin so future prod GPU dispatcher can call the same module.

Usage (executed via SSM RunShellScript on the GPU worker)::

    sudo /opt/sam/stand-alone-analyzer/.venv/bin/python3 \\
        /tmp/measure-defer.py \\
        --weights-uri s3://qpress-uploads/internal/sam/merged_m3/...pt \\
        --dataset-dir /opt/sam/dataset/scan6-100 \\
        --analysis-folder /opt/sam/runs/<RUN_ID> \\
        --run-id <RUN_ID> \\
        --scan-id <SCAN_ID>
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Ensure the repo `src/` is on sys.path. AMI lays it out at this path.
_REPO_SRC = Path("/opt/sam/stand-alone-analyzer/src")
if _REPO_SRC.exists() and str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

from flake_analysis.worker.measurement import (  # noqa: E402
    build_defer_payload,
    load_worker_env,
    resolve_model_meta,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SAM measurement defer launcher")
    p.add_argument(
        "--weights-uri",
        required=True,
        help="s3:// URI or local path to a .pt weights file",
    )
    p.add_argument(
        "--dataset-dir",
        required=True,
        help="local directory of .png input images",
    )
    p.add_argument(
        "--analysis-folder",
        required=True,
        help="local output folder for SAM results",
    )
    p.add_argument("--run-id", type=int, required=True)
    p.add_argument("--scan-id", type=int, required=True)
    p.add_argument(
        "--worker-env-file",
        default="/etc/flake-analysis-worker.env",
        help="systemd EnvironmentFile to inherit RDS creds from",
    )
    return p.parse_args()


async def _defer(payload: dict) -> int:
    # Imports happen AFTER load_worker_env() updates os.environ so that
    # DbSettings() picks up SAA_DB_*.
    from flake_analysis.worker.app import app

    # Force-import worker.tasks so @app.task decorators register run_sam
    # on this app instance before defer_async resolves it.
    import flake_analysis.worker.tasks  # noqa: F401

    async with app.open_async():
        job_id = await app.tasks["run_sam"].defer_async(**payload)
    return int(job_id)


def main() -> int:
    args = _parse_args()
    os.environ.update(load_worker_env(Path(args.worker_env_file)))

    model_meta = resolve_model_meta(args.weights_uri)
    payload = build_defer_payload(
        run_id=args.run_id,
        scan_id=args.scan_id,
        model_meta=model_meta,
        dataset_dir=Path(args.dataset_dir),
        analysis_folder=Path(args.analysis_folder),
    )

    job_id = asyncio.run(_defer(payload))
    print(f"job_id={job_id}")
    print(f"model_name={model_meta['name']}")
    print(f"model_sha256={model_meta['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
