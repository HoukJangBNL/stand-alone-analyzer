"""Build a tiny synthetic fixture for parity / end-to-end tests.

Layout produced::

    fixture_dir/
        raw_images/
            ix000_iy000.png
            ix001_iy000.png
            ...
        segmentation/
            annotations.json   (COCO with N images x 2 RLE-encoded blobs)

The ``segmentation/`` subdir matches the layout expected by
``flake_core.pipeline.domain_stats.run_domain_stats`` (the parent of the
annotations.json file becomes ``analysis_type``).

Color design
------------
Each image carries two visually distinct blobs whose target RGBs cycle
through four well-separated centers (≈``[60, 60, 60]``, ``[110, 80, 90]``,
``[150, 170, 110]``, ``[200, 200, 220]``). With ``n_images=5`` this produces
10 domains spanning enough RGB variance for a 2-component GMM to fit
without collapsing — see ``tests/parity/test_pipeline_e2e.py``.

Per plan v1 r9 §M3.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

import numpy as np
from PIL import Image
from pycocotools import mask as mask_util


# Four well-separated RGB centers (chosen so the GMM has real variance to fit).
_BLOB_COLORS: List[Tuple[int, int, int]] = [
    (60, 60, 60),
    (110, 80, 90),
    (150, 170, 110),
    (200, 200, 220),
]


def _encode_mask(mask: np.ndarray) -> dict:
    """COCO RLE-encode a uint8 binary mask, returning JSON-serializable dict."""
    fortran = np.asfortranarray(mask.astype(np.uint8))
    rle = mask_util.encode(fortran)
    counts = rle["counts"]
    if isinstance(counts, bytes):
        counts = counts.decode("ascii")
    return {"size": list(rle["size"]), "counts": counts}


def _draw_disk(
    img: np.ndarray,
    cx: int,
    cy: int,
    radius: int,
    color: Tuple[int, int, int],
) -> np.ndarray:
    """Stamp a filled disk into ``img`` and return the binary mask of the disk.

    Image-coordinate convention: ``img[y, x]`` for a (H, W, 3) array.
    """
    h, w = img.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    r2 = radius * radius
    y0 = max(0, cy - radius)
    y1 = min(h, cy + radius + 1)
    x0 = max(0, cx - radius)
    x1 = min(w, cx + radius + 1)
    for y in range(y0, y1):
        dy = y - cy
        for x in range(x0, x1):
            dx = x - cx
            if dx * dx + dy * dy <= r2:
                mask[y, x] = 1
                img[y, x] = color
    return mask


def build_fixture(
    out_dir: Path,
    *,
    n_images: int = 5,
    image_size: int = 200,
    seed: int = 0,
    blob_radius: int = 12,
) -> Tuple[Path, Path]:
    """Create raw_images/ + segmentation/annotations.json.

    Parameters
    ----------
    out_dir : Path
        Destination directory; created if missing.
    n_images : int
        Number of raw PNGs / annotation images to produce (default 5).
        Each image yields 2 blobs, so ``n_images=5`` → 10 domains total.
    image_size : int
        Square image side length in pixels (default 200).
    seed : int
        RNG seed for blob placement (default 0).
    blob_radius : int
        Disk radius in pixels (default 12 → area ~ pi * r^2 = 452 px).

    Returns
    -------
    tuple
        ``(raw_images_dir, annotations_path)``.
    """
    out_dir = Path(out_dir)
    raw_dir = out_dir / "raw_images"
    seg_dir = out_dir / "segmentation"
    raw_dir.mkdir(parents=True, exist_ok=True)
    seg_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(seed)

    images: List[dict] = []
    annotations: List[dict] = []
    ann_id = 1

    for img_idx in range(n_images):
        img_id = img_idx + 1
        # Slightly noisy gray background so background.npy is non-degenerate.
        bg_value = int(80 + rng.integers(-5, 6))
        img = np.full((image_size, image_size, 3), bg_value, dtype=np.uint8)

        # Pick two well-separated blob centers.
        margin = blob_radius + 3
        cx1 = int(rng.integers(margin, image_size // 2 - margin))
        cy1 = int(rng.integers(margin, image_size - margin))
        cx2 = int(rng.integers(image_size // 2 + margin, image_size - margin))
        cy2 = int(rng.integers(margin, image_size - margin))

        color1 = _BLOB_COLORS[(2 * img_idx) % len(_BLOB_COLORS)]
        color2 = _BLOB_COLORS[(2 * img_idx + 1) % len(_BLOB_COLORS)]

        m1 = _draw_disk(img, cx1, cy1, blob_radius, color1)
        m2 = _draw_disk(img, cx2, cy2, blob_radius, color2)

        file_name = f"ix{img_idx:03d}_iy000.png"
        Image.fromarray(img).save(raw_dir / file_name)

        images.append({
            "id": img_id,
            "file_name": file_name,
            "width": image_size,
            "height": image_size,
        })

        for cx, cy, mask in ((cx1, cy1, m1), (cx2, cy2, m2)):
            annotations.append({
                "id": ann_id,
                "image_id": img_id,
                "bbox": [
                    int(cx - blob_radius),
                    int(cy - blob_radius),
                    int(2 * blob_radius),
                    int(2 * blob_radius),
                ],
                "area": int(mask.sum()),
                "score": 0.95,
                "segmentation": _encode_mask(mask),
            })
            ann_id += 1

    coco = {"images": images, "annotations": annotations}
    ann_path = seg_dir / "annotations.json"
    ann_path.write_text(json.dumps(coco), encoding="utf-8")

    return raw_dir, ann_path
