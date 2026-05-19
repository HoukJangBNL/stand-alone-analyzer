"""Smoke tests for the Selector pipeline wrapper.

Per plan v1 r9 §M2 PR 2.3:
  * No-bounds passthrough → all domains accepted
  * Prereq guard → missing Domain Stats raises RuntimeError
  * area_min bound → fewer domains accepted, parquet written
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from flake_analysis.pipeline.selector import run_selector_step
from flake_analysis.state.manifest import (
    Manifest,
    StepEntry,
    load_manifest,
    save_manifest,
)


def _setup_fixture(tmp: Path, n: int = 100, with_sam2: bool = True) -> Path:
    """Create a fake stats.npz + manifest with completed domain_stats step."""
    analysis = tmp / "analysis"
    stats_dir = analysis / "02_domain_stats"
    stats_dir.mkdir(parents=True)
    stats_path = stats_dir / "stats.npz"

    rng = np.random.default_rng(0)
    arrays = {
        "repr_rgbs": rng.uniform(0, 255, size=(n, 3)).astype(np.float64),
        "std_pcts": rng.uniform(0, 50, size=(n, 3)).astype(np.float64),
        "areas": rng.integers(10, 1000, size=n).astype(np.float64),
        "flake_ids": np.arange(n, dtype=np.int64),
    }
    if with_sam2:
        arrays["sam2"] = rng.uniform(0, 1, size=n).astype(np.float64)
    np.savez(stats_path, **arrays)

    manifest = Manifest(
        analysis_folder=str(analysis),
        steps={
            "domain_stats": StepEntry(
                completed_at="2026-05-18T10:00:00Z",
                params={"repr_mode": "median", "raw_ext": ".png"},
                params_hash="sha256:abc",
                outputs={"stats_npz": "02_domain_stats/stats.npz"},
            ),
        },
    )
    save_manifest(manifest, analysis)
    return analysis


def test_run_selector_no_bounds_accepts_all():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        analysis = _setup_fixture(tmp, n=100)

        result = run_selector_step(analysis_folder=str(analysis))

        out_path = analysis / "03_selector" / "selection.parquet"
        assert out_path.exists(), "selection.parquet not written"

        # all None bounds → all accepted
        assert result["total_count"] == 100
        assert result["selected_count"] == 100

        df = pd.read_parquet(out_path)
        assert list(df.columns) == ["domain_id", "selected"]
        assert int(df["selected"].sum()) == 100

        # manifest updated
        m = load_manifest(str(analysis))
        assert "selector" in m.steps
        sel = m.steps["selector"]
        assert sel.completed_at is not None
        assert sel.params_hash is not None and sel.params_hash.startswith("sha256:")
        assert sel.outputs["selection_parquet"] == "03_selector/selection.parquet"
        # upstream linkage recorded
        assert sel.input_hashes["domain_stats_params_hash"] == "sha256:abc"


def test_run_selector_without_stats_raises():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        analysis = tmp / "analysis"
        analysis.mkdir()
        with pytest.raises(RuntimeError, match="Domain Stats"):
            run_selector_step(analysis_folder=str(analysis))


def test_run_selector_with_area_min_filters():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        analysis = _setup_fixture(tmp, n=100)

        # Re-load NPZ to count expected acceptances under area_min=500
        npz = np.load(analysis / "02_domain_stats" / "stats.npz")
        expected = int((npz["areas"] >= 500.0).sum())
        assert 0 < expected < 100, "fixture RNG produced degenerate distribution"

        result = run_selector_step(analysis_folder=str(analysis), area_min=500.0)

        assert result["selected_count"] == expected
        assert result["total_count"] == 100

        df = pd.read_parquet(analysis / "03_selector" / "selection.parquet")
        assert int(df["selected"].sum()) == expected


def test_run_selector_sam2_missing_ignores_bounds():
    """If NPZ has no 'sam2' column, sam2 bounds are ignored (allow_missing)."""
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        analysis = _setup_fixture(tmp, n=50, with_sam2=False)

        # sam2_min=0.99 would normally exclude almost everything,
        # but with sam2 missing, the bound is ignored.
        result = run_selector_step(
            analysis_folder=str(analysis), sam2_min=0.99, sam2_max=1.0,
        )

        assert result["selected_count"] == 50
        assert result["total_count"] == 50


def test_values_for_axis_returns_correct_columns():
    """Axis dropdown mapping → correct stats column for each axis name."""
    from flake_analysis.ui.tab_selector import _values_for_axis

    rng = np.random.default_rng(42)
    n = 8
    rgb = rng.uniform(0, 255, size=(n, 3)).astype(np.float64)
    std = rng.uniform(0, 50, size=(n, 3)).astype(np.float64)
    areas = rng.integers(10, 1000, size=n).astype(np.float64)
    sam2 = rng.uniform(0, 1, size=n).astype(np.float64)
    flake_ids = np.arange(n, dtype=np.int64)

    stats = {
        "repr_rgbs": rgb,
        "std_pcts": std,
        "areas": areas,
        "sam2": sam2,
        "flake_ids": flake_ids,
    }

    np.testing.assert_array_equal(_values_for_axis(stats, "R"), rgb[:, 0])
    np.testing.assert_array_equal(_values_for_axis(stats, "G"), rgb[:, 1])
    np.testing.assert_array_equal(_values_for_axis(stats, "B"), rgb[:, 2])
    np.testing.assert_array_equal(_values_for_axis(stats, "std_r"), std[:, 0])
    np.testing.assert_array_equal(_values_for_axis(stats, "std_g"), std[:, 1])
    np.testing.assert_array_equal(_values_for_axis(stats, "std_b"), std[:, 2])
    np.testing.assert_array_equal(_values_for_axis(stats, "area"), areas)
    np.testing.assert_array_equal(_values_for_axis(stats, "sam2"), sam2)

    # Unknown axis raises a ValueError so dropdown bugs surface loudly.
    with pytest.raises(ValueError, match="unknown axis"):
        _values_for_axis(stats, "bogus")

    # sam2 column missing → zeros fallback (allow_missing semantics, mirrors
    # the filter behavior in _apply_filter).
    stats_no_sam = {k: v for k, v in stats.items() if k != "sam2"}
    np.testing.assert_array_equal(
        _values_for_axis(stats_no_sam, "sam2"), np.zeros(n)
    )
