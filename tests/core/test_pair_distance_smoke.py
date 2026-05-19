"""Smoke test: verify ``process_image`` and ``union_find_islands`` are wired up.

Exhaustive parity testing happens in M4 (validation harness).
"""
from __future__ import annotations

import numpy as np
import pycocotools.mask as mask_util

from flake_analysis.core.image_processing import process_image, union_find_islands


def _encode_mask(mask: np.ndarray) -> dict:
    """Encode a 2D uint8 binary mask as a COCO RLE dict (counts as str)."""
    fortran = np.asfortranarray(mask.astype(np.uint8))
    rle = mask_util.encode(fortran)
    counts = rle["counts"]
    if isinstance(counts, bytes):
        counts = counts.decode("ascii")
    return {"size": list(rle["size"]), "counts": counts}


def test_process_image_callable() -> None:
    """Empty annotation list returns empty results (no crash)."""
    pairs, border, islands = process_image(
        anns=[], img_w=100, img_h=100, r_max=10.0, min_area_px=1
    )
    assert pairs == []
    assert border == []
    assert islands == []


def test_process_image_two_adjacent_masks() -> None:
    """Two small adjacent masks produce one pair and one merged island."""
    h, w = 50, 50
    m1 = np.zeros((h, w), dtype=np.uint8)
    m1[10:20, 10:20] = 1
    m2 = np.zeros((h, w), dtype=np.uint8)
    m2[10:20, 21:31] = 1  # 1px gap

    anns = [
        {
            "id": 1,
            "image_id": 1,
            "bbox": [10, 10, 10, 10],
            "area": int(m1.sum()),
            "segmentation": _encode_mask(m1),
        },
        {
            "id": 2,
            "image_id": 1,
            "bbox": [21, 10, 10, 10],
            "area": int(m2.sum()),
            "segmentation": _encode_mask(m2),
        },
    ]

    pairs, _border, islands = process_image(
        anns=anns, img_w=w, img_h=h, r_max=20.0, min_area_px=1, d_touch_px=2.0
    )
    assert len(pairs) == 1
    a, b, d = pairs[0]
    assert (a, b) == (1, 2)
    assert d <= 2.0
    assert len(islands) == 1
    assert sorted(islands[0]["flake_ids"]) == [1, 2]


def test_union_find_islands_singleton_preserved() -> None:
    """Domains absent from pairs remain singletons (review_algorithm.md T2)."""
    flake_ids = [1, 2, 3, 4]
    pairs = [(1, 2, 1.0)]  # only 1-2 touch
    out = union_find_islands(flake_ids, pairs, d_touch_px=2.0)
    # one merged group {1,2} and two singletons {3},{4}
    sizes = sorted(len(g) for g in out)
    assert sizes == [1, 1, 2]
    flat = sorted(fid for g in out for fid in g)
    assert flat == flake_ids
