"""Verify each pipeline wrapper accepts ``progress_callback`` and emits at least
2 calls with monotonic non-decreasing pct ending at 1.0. Backward compat is
verified by re-invoking each wrapper without the kwarg (must not raise).

Plan: end-to-end progress visibility (v0.1.0 -> v0.2.0).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Callable, List, Tuple

import numpy as np
import pandas as pd
import pycocotools.mask as mask_util
from PIL import Image


def _capture() -> Tuple[List[Tuple[float, str]], Callable[[float, str], None]]:
    """Return (captured_list, callback) — the callback appends ``(pct, msg)`` tuples."""
    captured: List[Tuple[float, str]] = []

    def cb(pct: float, msg: str) -> None:
        captured.append((pct, msg))

    return captured, cb


def _assert_monotonic_to_one(captured: List[Tuple[float, str]]) -> None:
    """Sanity-check the captured progress sequence."""
    assert len(captured) >= 2, f"expected ≥2 callbacks, got {len(captured)}"
    pcts = [p for p, _ in captured]
    assert all(0.0 <= p <= 1.0 for p in pcts), f"pct out of [0, 1]: {pcts}"
    assert pcts == sorted(pcts), f"pct sequence not monotonic: {pcts}"
    assert pcts[-1] == 1.0, f"last pct should be 1.0, got {pcts[-1]}"


# --- run_background ---------------------------------------------------------


def _make_raw_dir(tmp_path: Path, n: int = 8) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    rng = np.random.default_rng(0)
    for i in range(n):
        arr = rng.integers(0, 256, size=(50, 50, 3), dtype=np.uint8)
        Image.fromarray(arr).save(raw / f"img_{i:03d}.png")
    return raw


def test_run_background_emits_progress() -> None:
    from flake_analysis.core.pipeline.background import run_background

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        raw = _make_raw_dir(tmp_path, n=10)

        captured, cb = _capture()
        run_background(
            raw_images_dir=str(raw),
            output_path=str(tmp_path / "bg.npy"),
            seed=0,
            max_images=10,
            gaussian_sigma=0.0,
            progress_callback=cb,
        )

        _assert_monotonic_to_one(captured)


def test_run_background_works_without_callback() -> None:
    """Backward compat: omitting the kwarg must not raise."""
    from flake_analysis.core.pipeline.background import run_background

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        raw = _make_raw_dir(tmp_path, n=5)

        run_background(
            raw_images_dir=str(raw),
            output_path=str(tmp_path / "bg.npy"),
            seed=0,
            max_images=5,
            gaussian_sigma=0.0,
        )  # no callback — must not raise


# --- run_domain_proximity ---------------------------------------------------


def _encode_mask(mask: np.ndarray) -> dict:
    fortran = np.asfortranarray(mask.astype(np.uint8))
    rle = mask_util.encode(fortran)
    counts = rle["counts"]
    if isinstance(counts, bytes):
        counts = counts.decode("ascii")
    return {"size": list(rle["size"]), "counts": counts}


def _make_two_image_annotations(tmp_path: Path) -> Path:
    h, w = 50, 50
    m1 = np.zeros((h, w), dtype=np.uint8); m1[10:20, 10:20] = 1
    m2 = np.zeros((h, w), dtype=np.uint8); m2[10:20, 21:31] = 1
    m3 = np.zeros((h, w), dtype=np.uint8); m3[5:15, 5:15] = 1

    coco = {
        "images": [
            {"id": 1, "file_name": "a.png", "width": w, "height": h},
            {"id": 2, "file_name": "b.png", "width": w, "height": h},
        ],
        "annotations": [
            {"id": 1, "image_id": 1, "bbox": [10, 10, 10, 10],
             "area": int(m1.sum()), "segmentation": _encode_mask(m1)},
            {"id": 2, "image_id": 1, "bbox": [21, 10, 10, 10],
             "area": int(m2.sum()), "segmentation": _encode_mask(m2)},
            {"id": 3, "image_id": 2, "bbox": [5, 5, 10, 10],
             "area": int(m3.sum()), "segmentation": _encode_mask(m3)},
        ],
    }
    ann_path = tmp_path / "annotations.json"
    ann_path.write_text(json.dumps(coco))
    return ann_path


def test_run_domain_proximity_emits_progress() -> None:
    from flake_analysis.core.pipeline.domain_proximity import run_domain_proximity

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        ann_path = _make_two_image_annotations(tmp_path)
        captured, cb = _capture()
        run_domain_proximity(
            annotations_path=ann_path,
            output_dir=tmp_path / "out",
            r_max_px=20.0,
            min_area_px=1,
            d_touch_px=2.0,
            link_distance_um=1.0,
            pixel_size_um=0.5,
            workers=1,
            progress_callback=cb,
        )

        _assert_monotonic_to_one(captured)


def test_run_domain_proximity_works_without_callback() -> None:
    from flake_analysis.core.pipeline.domain_proximity import run_domain_proximity

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        ann_path = _make_two_image_annotations(tmp_path)
        run_domain_proximity(
            annotations_path=ann_path,
            output_dir=tmp_path / "out",
            r_max_px=20.0,
            min_area_px=1,
            workers=1,
        )  # no callback — must not raise


# --- run_selector -----------------------------------------------------------


def _write_synthetic_stats_npz(path: Path, n: int = 100) -> None:
    rng = np.random.default_rng(0)
    np.savez(
        path,
        repr_rgbs=rng.uniform(0, 255, size=(n, 3)).astype(np.float64),
        std_pcts=rng.uniform(0, 50, size=(n, 3)).astype(np.float64),
        areas=np.arange(n, dtype=np.int32) * 10,
        flake_ids=np.arange(n, dtype=np.int64),
    )


def test_run_selector_emits_progress() -> None:
    from flake_analysis.core.pipeline.selector import run_selector

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        npz_path = tmp_path / "stats.npz"
        _write_synthetic_stats_npz(npz_path, n=50)

        captured, cb = _capture()
        run_selector(
            stats_npz_path=npz_path,
            output_path=tmp_path / "selection.parquet",
            area_min=100.0,
            progress_callback=cb,
        )

        _assert_monotonic_to_one(captured)


def test_run_selector_works_without_callback() -> None:
    from flake_analysis.core.pipeline.selector import run_selector

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        npz_path = tmp_path / "stats.npz"
        _write_synthetic_stats_npz(npz_path, n=10)
        run_selector(
            stats_npz_path=npz_path,
            output_path=tmp_path / "selection.parquet",
        )  # no callback — must not raise


# --- run_clustering ---------------------------------------------------------


def _make_two_blob_npz(path: Path) -> None:
    rng = np.random.default_rng(7)
    blob_a = rng.normal(loc=[50.0, 50.0, 50.0], scale=2.0, size=(40, 3))
    blob_b = rng.normal(loc=[200.0, 200.0, 200.0], scale=2.0, size=(40, 3))
    np.savez(
        path,
        repr_rgbs=np.vstack([blob_a, blob_b]).astype(np.float64),
        std_pcts=rng.uniform(0, 30, size=(80, 3)),
        areas=np.full(80, 500, dtype=np.int32),
        flake_ids=np.arange(80, dtype=np.int64),
    )


def _all_selected_parquet(path: Path, n: int) -> None:
    pd.DataFrame(
        {"domain_id": np.arange(n, dtype=np.int64), "selected": [True] * n}
    ).to_parquet(path, engine="pyarrow", index=False)


def test_run_clustering_emits_progress() -> None:
    from flake_analysis.core.pipeline.clustering import run_clustering

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        npz_path = tmp_path / "stats.npz"
        sel_path = tmp_path / "selection.parquet"
        _make_two_blob_npz(npz_path)
        _all_selected_parquet(sel_path, n=80)

        captured, cb = _capture()
        run_clustering(
            stats_npz_path=npz_path,
            selection_parquet_path=sel_path,
            seed_groups=[
                {"name": "dark", "domain_ids": [0, 1, 2]},
                {"name": "light", "domain_ids": [40, 41, 42]},
            ],
            output_dir=tmp_path / "out",
            rgb_threshold=0.5,
            progress_callback=cb,
        )

        _assert_monotonic_to_one(captured)


def test_run_clustering_works_without_callback() -> None:
    from flake_analysis.core.pipeline.clustering import run_clustering

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        npz_path = tmp_path / "stats.npz"
        sel_path = tmp_path / "selection.parquet"
        _make_two_blob_npz(npz_path)
        _all_selected_parquet(sel_path, n=80)

        run_clustering(
            stats_npz_path=npz_path,
            selection_parquet_path=sel_path,
            seed_groups=[
                {"name": "dark", "domain_ids": [0, 1, 2]},
                {"name": "light", "domain_ids": [40, 41, 42]},
            ],
            output_dir=tmp_path / "out",
        )  # no callback — must not raise
