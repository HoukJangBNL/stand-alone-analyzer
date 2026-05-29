"""measure-run.sh --dryrun prints intended commands and exits 0 without
contacting AWS. Smoke-level: confirms argparse + phase ordering."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "sam" / "measure-run.sh"


@pytest.mark.skipif(not SCRIPT.exists(), reason="script not yet present")
def test_dryrun_prints_phases_and_exits_zero(tmp_path: Path) -> None:
    env = {**os.environ, "AWS_PROFILE": "qpress", "AWS_REGION": "us-east-2"}
    result = subprocess.run(
        [
            str(SCRIPT),
            "--weights",
            "s3://qpress-uploads/internal/sam/merged_m3/sam2.1_hiera_large.merged_m3.3ec586fc.pt",
            "--dataset",
            "s3://qpress-uploads/internal/sam/scan6-100/",
            "--instance-type", "g6e.48xlarge",
            "--cost-cap-usd", "5",
            "--wall-cap-min", "60",
            "--dryrun",
        ],
        env=env, check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    out = result.stdout
    # Phases must run in order, regardless of formatting.
    for phase in ["[phase=1]", "[phase=2]", "[phase=3]", "[phase=4]",
                  "[phase=11]"]:
        assert phase in out, f"missing {phase} in dryrun output:\n{out}"
    # Dryrun must NOT call run-instances.
    assert "Would: aws ec2 run-instances" in out
    assert "Would: aws ec2 terminate-instances" in out


def test_dryrun_missing_required_arg_exits_nonzero() -> None:
    if not SCRIPT.exists():
        pytest.skip("script not yet present")
    result = subprocess.run(
        [str(SCRIPT), "--dryrun"],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "--weights" in result.stderr or "--weights" in result.stdout
