"""End-to-end smoke for pipeline wrappers (background + domain_proximity)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pycocotools.mask as mask_util
from PIL import Image

from flake_analysis.core.pipeline import run_background, run_domain_proximity


def _create_fixture_images(tmpdir: Path, n: int) -> None:
    rng = np.random.default_rng(0)
    for i in range(n):
        arr = rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8)
        Image.fromarray(arr).save(tmpdir / f"img_{i:03d}.png")


def _encode_mask(mask: np.ndarray) -> dict:
    fortran = np.asfortranarray(mask.astype(np.uint8))
    rle = mask_util.encode(fortran)
    counts = rle["counts"]
    if isinstance(counts, bytes):
        counts = counts.decode("ascii")
    return {"size": list(rle["size"]), "counts": counts}


def test_run_background_writes_npy() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        _create_fixture_images(raw_dir, n=10)

        out_path = tmp_path / "background.npy"
        result = run_background(
            raw_images_dir=raw_dir,
            output_path=out_path,
            seed=0,
            max_images=5,
            gaussian_sigma=0.0,
        )

        assert out_path.exists()
        loaded = np.load(out_path)
        np.testing.assert_array_equal(result["array"], loaded)
        assert result["params"]["seed"] == 0
        assert result["params_hash"].startswith("sha256:")


def test_run_domain_proximity_two_image_fixture() -> None:
    """Build a 2-image annotations.json and verify the wrapper writes both parquets."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        h, w = 50, 50
        m1 = np.zeros((h, w), dtype=np.uint8)
        m1[10:20, 10:20] = 1
        m2 = np.zeros((h, w), dtype=np.uint8)
        m2[10:20, 21:31] = 1
        m3 = np.zeros((h, w), dtype=np.uint8)
        m3[35:45, 35:45] = 1  # isolated singleton in image 1

        m4 = np.zeros((h, w), dtype=np.uint8)
        m4[5:15, 5:15] = 1  # standalone domain in image 2

        coco = {
            "images": [
                {"id": 1, "file_name": "a.png", "width": w, "height": h},
                {"id": 2, "file_name": "b.png", "width": w, "height": h},
            ],
            "annotations": [
                {
                    "id": 1, "image_id": 1, "bbox": [10, 10, 10, 10],
                    "area": int(m1.sum()), "segmentation": _encode_mask(m1),
                },
                {
                    "id": 2, "image_id": 1, "bbox": [21, 10, 10, 10],
                    "area": int(m2.sum()), "segmentation": _encode_mask(m2),
                },
                {
                    "id": 3, "image_id": 1, "bbox": [35, 35, 10, 10],
                    "area": int(m3.sum()), "segmentation": _encode_mask(m3),
                },
                {
                    "id": 4, "image_id": 2, "bbox": [5, 5, 10, 10],
                    "area": int(m4.sum()), "segmentation": _encode_mask(m4),
                },
            ],
        }

        ann_path = tmp_path / "annotations.json"
        ann_path.write_text(json.dumps(coco))
        out_dir = tmp_path / "out"

        result = run_domain_proximity(
            annotations_path=ann_path,
            output_dir=out_dir,
            r_max_px=20.0,
            min_area_px=1,
            d_touch_px=2.0,
            link_distance_um=1.0,  # 2 px at 0.5 um/px
            pixel_size_um=0.5,
            workers=1,
        )

        assert result["distances_path"].exists()
        assert result["flake_assignments_path"].exists()
        assert result["n_domains"] == 4

        distances = pd.read_parquet(result["distances_path"])
        assert {"domain_id_a", "domain_id_b", "distance_px", "distance_um"} <= set(
            distances.columns
        )
        # 1-2 are adjacent within image 1 → at least one pair recorded
        pair_set = set(zip(distances["domain_id_a"], distances["domain_id_b"]))
        assert (1, 2) in pair_set

        flakes = pd.read_parquet(result["flake_assignments_path"])
        assert set(flakes["domain_id"]) == {1, 2, 3, 4}
        # Domain 1 and 2 share a flake_id (touching); 3 and 4 are singletons.
        flake_for = dict(zip(flakes["domain_id"], flakes["flake_id"]))
        assert flake_for[1] == flake_for[2]
        assert flake_for[3] != flake_for[1]
        assert flake_for[4] != flake_for[1]
