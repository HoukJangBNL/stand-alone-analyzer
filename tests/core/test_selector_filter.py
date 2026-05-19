"""Selector 5-metric filter math is correct."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from flake_analysis.core.pipeline import run_selector


def _write_synthetic_npz(path: Path, n: int, *, with_sam2: bool = True, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    areas = np.arange(n, dtype=np.int32) * 10  # 0, 10, 20, ... predictable
    payload = {
        "repr_rgbs": rng.uniform(0, 255, size=(n, 3)).astype(np.float64),
        "std_pcts": rng.uniform(0, 50, size=(n, 3)).astype(np.float64),
        "areas": areas,
        "flake_ids": np.arange(n, dtype=np.int64),
    }
    if with_sam2:
        payload["sam2"] = rng.uniform(0, 1, size=n).astype(np.float64)
    np.savez(path, **payload)
    return payload


def test_no_bounds_passes_all():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        npz_path = tmp / "stats.npz"
        out_path = tmp / "selection.parquet"
        _write_synthetic_npz(npz_path, n=100)

        result = run_selector(npz_path, output_path=out_path)
        assert result["selected_count"] == 100
        assert result["total_count"] == 100

        df = pd.read_parquet(out_path)
        assert df.shape == (100, 2)
        assert df["selected"].all()


def test_area_min_filter():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        npz_path = tmp / "stats.npz"
        out_path = tmp / "selection.parquet"
        payload = _write_synthetic_npz(npz_path, n=100)
        areas = payload["areas"]

        result = run_selector(npz_path, output_path=out_path, area_min=500)
        expected = int(np.sum(areas >= 500))
        assert result["selected_count"] == expected


def test_bidirectional_area_bound():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        npz_path = tmp / "stats.npz"
        out_path = tmp / "selection.parquet"
        payload = _write_synthetic_npz(npz_path, n=100)
        areas = payload["areas"]

        result = run_selector(
            npz_path,
            output_path=out_path,
            area_min=200,
            area_max=400,
        )
        expected = int(np.sum((areas >= 200) & (areas <= 400)))
        assert result["selected_count"] == expected

        df = pd.read_parquet(out_path)
        passed = df.loc[df["selected"], "domain_id"].astype(int).to_numpy()
        # Domain ids 20..40 inclusive given areas = 10 * domain_id.
        assert set(passed) == {i for i in range(100) if 200 <= i * 10 <= 400}


def test_sam2_missing_with_bound_warns_and_passes():
    """allow_missing=True semantics: sam2 bounds ignored when column absent."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        npz_path = tmp / "stats.npz"
        out_path = tmp / "selection.parquet"
        _write_synthetic_npz(npz_path, n=50, with_sam2=False)

        result = run_selector(
            npz_path,
            output_path=out_path,
            sam2_min=0.9,  # Would filter heavily if applied; should be ignored.
        )
        assert result["selected_count"] == 50


def test_sam2_filter_when_present():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        npz_path = tmp / "stats.npz"
        out_path = tmp / "selection.parquet"
        payload = _write_synthetic_npz(npz_path, n=200)
        sam2 = payload["sam2"]

        result = run_selector(npz_path, output_path=out_path, sam2_min=0.5)
        expected = int(np.sum(sam2 >= 0.5))
        assert result["selected_count"] == expected


def test_combined_5_metric_filter():
    """All 5 metrics chained together — AND-reduce should match manual mask."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        npz_path = tmp / "stats.npz"
        out_path = tmp / "selection.parquet"
        payload = _write_synthetic_npz(npz_path, n=300, seed=42)

        bounds = dict(
            area_min=500,
            area_max=2500,
            std_r_min=5.0,
            std_r_max=40.0,
            std_g_min=5.0,
            std_b_max=45.0,
            sam2_min=0.2,
        )
        result = run_selector(npz_path, output_path=out_path, **bounds)

        manual = (
            (payload["areas"] >= 500)
            & (payload["areas"] <= 2500)
            & (payload["std_pcts"][:, 0] >= 5.0)
            & (payload["std_pcts"][:, 0] <= 40.0)
            & (payload["std_pcts"][:, 1] >= 5.0)
            & (payload["std_pcts"][:, 2] <= 45.0)
            & (payload["sam2"] >= 0.2)
        )
        assert result["selected_count"] == int(manual.sum())
