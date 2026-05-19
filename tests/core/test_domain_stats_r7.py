"""r7: bg_mode removed, background mandatory, NPZ path hardcoded."""
from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

import pytest

from flake_analysis.core.pipeline import run_domain_stats


def test_run_domain_stats_signature_drops_bg_mode_and_output_path():
    """bg_mode and output_path are removed; analysis_folder + background_path required."""
    sig = inspect.signature(run_domain_stats)
    params = sig.parameters

    # Removed parameters.
    assert "bg_mode" not in params
    assert "output_path" not in params

    # New required parameters.
    assert "analysis_folder" in params
    assert "background_path" in params


def test_run_domain_stats_rejects_none_background():
    """Passing background_path=None raises ValueError before any file load."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        with pytest.raises(ValueError, match="background_path is required"):
            run_domain_stats(
                annotations_path=tmp / "annotations.json",
                raw_images_dir=tmp,
                background_path=None,  # type: ignore[arg-type]
                analysis_folder=tmp,
            )


def test_run_domain_stats_rejects_missing_background_file():
    """Pointing background_path at a non-existent file raises FileNotFoundError."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        with pytest.raises(FileNotFoundError, match="background_path does not exist"):
            run_domain_stats(
                annotations_path=tmp / "annotations.json",
                raw_images_dir=tmp,
                background_path=tmp / "nope.png",
                analysis_folder=tmp,
            )
