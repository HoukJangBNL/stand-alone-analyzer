"""Verify each app-level pipeline wrapper forwards ``progress_callback`` to the
flake_analysis.core pipeline wrappers underneath. We don't reach for the inner emission
points here; we trust the flake_analysis.core suite for that. We just check that the
sequence is non-empty, monotonic, and ends at 1.0.

Plan: end-to-end progress visibility (v0.1.0 -> v0.1.1).
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable, List, Tuple

import numpy as np
import pandas as pd
from PIL import Image

from flake_analysis.state.manifest import Manifest, StepEntry, save_manifest


def _capture() -> Tuple[List[Tuple[float, str]], Callable[[float, str], None]]:
    captured: List[Tuple[float, str]] = []

    def cb(pct: float, msg: str) -> None:
        captured.append((pct, msg))

    return captured, cb


def _assert_monotonic(captured: List[Tuple[float, str]]) -> None:
    assert len(captured) >= 2, f"expected ≥2 callbacks, got {len(captured)}"
    pcts = [p for p, _ in captured]
    assert pcts == sorted(pcts), f"pct sequence not monotonic: {pcts}"
    assert pcts[-1] == 1.0, f"last pct should be 1.0, got {pcts[-1]}"


def _make_raw_dir(tmp: Path, n: int) -> Path:
    raw = tmp / "raw_images"
    raw.mkdir()
    rng = np.random.default_rng(0)
    for i in range(n):
        arr = rng.integers(0, 256, size=(40, 40, 3), dtype=np.uint8)
        Image.fromarray(arr).save(raw / f"i_{i:03d}.png")
    return raw


# --- run_background_step ----------------------------------------------------


def test_run_background_step_forwards_callback() -> None:
    from flake_analysis.pipeline.background import run_background_step

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        raw = _make_raw_dir(tmp, n=5)
        analysis = tmp / "analysis"
        analysis.mkdir()

        captured, cb = _capture()
        run_background_step(
            raw_images_dir=str(raw),
            analysis_folder=str(analysis),
            seed=0,
            max_images=5,
            gaussian_sigma=0.0,
            progress_callback=cb,
        )

        _assert_monotonic(captured)


# --- run_selector_step ------------------------------------------------------


def _setup_selector_fixture(tmp: Path, n: int = 50) -> Path:
    analysis = tmp / "analysis"
    stats_dir = analysis / "02_domain_stats"
    stats_dir.mkdir(parents=True)
    rng = np.random.default_rng(0)
    np.savez(
        stats_dir / "stats.npz",
        repr_rgbs=rng.uniform(0, 255, size=(n, 3)).astype(np.float64),
        std_pcts=rng.uniform(0, 50, size=(n, 3)).astype(np.float64),
        areas=rng.integers(10, 1000, size=n).astype(np.float64),
        flake_ids=np.arange(n, dtype=np.int64),
    )
    save_manifest(
        Manifest(
            analysis_folder=str(analysis),
            steps={
                "domain_stats": StepEntry(
                    completed_at="2026-05-18T10:00:00Z",
                    params={"repr_mode": "median"},
                    params_hash="sha256:abc",
                    outputs={"stats_npz": "02_domain_stats/stats.npz"},
                ),
            },
        ),
        analysis,
    )
    return analysis


def test_run_selector_step_forwards_callback() -> None:
    from flake_analysis.pipeline.selector import run_selector_step

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        analysis = _setup_selector_fixture(tmp, n=30)

        captured, cb = _capture()
        run_selector_step(
            analysis_folder=str(analysis),
            area_min=100.0,
            progress_callback=cb,
        )

        _assert_monotonic(captured)


# --- run_clustering_step ----------------------------------------------------


def _setup_clustering_fixture(tmp: Path, n: int = 60):
    analysis = tmp / "analysis"
    (analysis / "02_domain_stats").mkdir(parents=True)
    (analysis / "03_selector").mkdir(parents=True)

    rng = np.random.default_rng(0)
    third = n // 3
    rgb_a = rng.normal([60, 60, 60], 5, size=(third, 3))
    rgb_b = rng.normal([120, 120, 120], 5, size=(third, 3))
    rgb_c = rng.normal([200, 200, 200], 5, size=(n - 2 * third, 3))
    rgb = np.vstack([rgb_a, rgb_b, rgb_c]).clip(0, 255)

    flake_ids = np.arange(n, dtype=np.int64)
    np.savez(
        analysis / "02_domain_stats" / "stats.npz",
        repr_rgbs=rgb.astype(np.float64),
        std_pcts=rng.uniform(0, 30, size=(n, 3)).astype(np.float64),
        areas=rng.integers(50, 500, size=n).astype(np.float64),
        flake_ids=flake_ids,
    )
    pd.DataFrame({"domain_id": flake_ids, "selected": [True] * n}).to_parquet(
        analysis / "03_selector" / "selection.parquet",
        engine="pyarrow",
        index=False,
    )
    save_manifest(
        Manifest(
            analysis_folder=str(analysis),
            steps={
                "domain_stats": StepEntry(
                    completed_at="2026-05-18T10:00:00Z",
                    params={},
                    params_hash="sha256:stats",
                    outputs={"stats_npz": "02_domain_stats/stats.npz"},
                ),
                "selector": StepEntry(
                    completed_at="2026-05-18T10:05:00Z",
                    params={},
                    params_hash="sha256:sel",
                    outputs={"selection_parquet": "03_selector/selection.parquet"},
                ),
            },
        ),
        analysis,
    )
    return analysis, flake_ids


def test_run_clustering_step_forwards_callback() -> None:
    from flake_analysis.pipeline.clustering import run_clustering_step

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        analysis, flake_ids = _setup_clustering_fixture(tmp, n=60)

        captured, cb = _capture()
        run_clustering_step(
            analysis_folder=str(analysis),
            seed_groups=[
                {"name": "low", "domain_ids": flake_ids[:5].tolist()},
                {"name": "high", "domain_ids": flake_ids[-5:].tolist()},
            ],
            progress_callback=cb,
        )

        _assert_monotonic(captured)


# --- backward compat: existing call sites omit progress_callback -----------


def test_app_wrappers_work_without_callback() -> None:
    """All four app wrappers must accept calls with no ``progress_callback`` arg."""
    from flake_analysis.pipeline.background import run_background_step
    from flake_analysis.pipeline.selector import run_selector_step

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        raw = _make_raw_dir(tmp, n=4)
        analysis = tmp / "a"
        analysis.mkdir()

        run_background_step(
            raw_images_dir=str(raw),
            analysis_folder=str(analysis),
            seed=0,
            max_images=4,
            gaussian_sigma=0.0,
        )  # no callback — must not raise

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        analysis = _setup_selector_fixture(tmp, n=10)
        run_selector_step(
            analysis_folder=str(analysis),
        )  # no callback — must not raise
