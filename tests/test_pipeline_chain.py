"""End-to-end ordering: domain_stats requires background first."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from flake_analysis.pipeline.domain_stats import run_domain_stats_step


def test_domain_stats_without_background_raises():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        analysis = tmp / "analysis"
        analysis.mkdir()
        # No background run: should raise RuntimeError mentioning Background.
        with pytest.raises(RuntimeError, match="Background"):
            run_domain_stats_step(
                raw_images_dir=str(tmp),
                annotations_path=str(tmp / "annotations.json"),
                analysis_folder=str(analysis),
            )
