#!/usr/bin/env python3
"""One-shot GPU launch via the prod ladder (launcher._launch_one).

Reuses the EXACT instance-type ladder + multi-AZ + spot/on-demand
fallback that the prod 1-click dispatcher uses (T7q), so the
measurement path and prod path never diverge. Prints the launched
instance id to stdout.

Usage (called by measure-run.sh phase 4):
    AWS_PROFILE=qpress PYTHONUNBUFFERED=1 python3 scripts/sam/measure-launch.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure the repo src/ is on sys.path for local imports.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import boto3
from flake_analysis.worker.launcher import (
    AWS_REGION,
    GpuCapacityUnavailable,
    _launch_one,
)

# Enable INFO logging so we see the full ladder trace from launcher._launch_one.
logging.basicConfig(
    level=logging.INFO,
    format="[launcher] %(message)s",
    stream=sys.stderr,  # trace to stderr, instance id to stdout
)


def main() -> int:
    """Launch one GPU worker via the prod instance-type ladder.

    Returns:
        0 on success (instance id written to stdout).
        5 on capacity drought (all 4 tiers × 3 AZ × 2 markets exhausted).
    """
    ec2 = boto3.client("ec2", region_name=AWS_REGION)
    try:
        instance_id = _launch_one(ec2)  # full ladder: 8/4/4/1 GPU, all AZs, spot→OD
    except GpuCapacityUnavailable as e:
        print(f"CAPACITY_DROUGHT: {e}", file=sys.stderr)
        return 5
    print(instance_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
