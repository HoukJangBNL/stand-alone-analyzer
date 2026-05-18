"""Smoke tests for ``flake_analysis.ui._image_preview`` (PR v0.1.2).

Exercises the pure-Python helpers (no Streamlit boot):
  * load_annotations_index — direct json.loads fallback
  * crop_for_domain — bbox + padding + clamp
  * v0.1.4: decode_segmentation_mask, contours_for_mask, build_image_preview_figure
"""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from pycocotools import mask as mask_util

from flake_analysis.ui._image_preview import (
    BOUNDARY_COLOR,
    build_image_preview_figure,
    contours_for_mask,
    crop_for_domain,
    crop_for_domain_with_mask,
    decode_segmentation_mask,
    load_annotations_index,
)


def _write_fixture(tmp: Path) -> tuple[Path, Path]:
    """Create raw_images/ + annotations.json. Returns (raw_dir, ann_path)."""
    raw = tmp / "raw_images"
    raw.mkdir()
    img = Image.fromarray(
        (np.random.default_rng(0).integers(0, 255, size=(64, 64, 3))
         .astype(np.uint8))
    )
    img.save(raw / "ix000_iy001.png")

    ann_path = tmp / "annotations.json"
    ann_path.write_text(
        json.dumps(
            {
                "images": [
                    {"id": 1, "file_name": "ix000_iy001.png", "width": 64, "height": 64}
                ],
                "annotations": [
                    {
                        "id": 100,
                        "image_id": 1,
                        "bbox": [10, 10, 20, 20],  # x, y, w, h
                        "area": 400,
                        "score": 0.9,
                    },
                    {
                        "id": 101,
                        "image_id": 1,
                        "bbox": [50, 50, 30, 30],  # extends beyond image
                        "area": 900,
                        "score": 0.8,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return raw, ann_path


def test_load_annotations_index(tmp_path):
    _, ann_path = _write_fixture(tmp_path)
    annotations, images = load_annotations_index(ann_path)
    assert set(annotations.keys()) == {100, 101}
    assert images[1]["file_name"] == "ix000_iy001.png"
    assert annotations[100]["bbox"] == [10, 10, 20, 20]


def test_load_annotations_index_missing_file(tmp_path):
    annotations, images = load_annotations_index(tmp_path / "nope.json")
    assert annotations == {}
    assert images == {}


def test_crop_for_domain_basic(tmp_path):
    raw, ann_path = _write_fixture(tmp_path)
    annotations, images = load_annotations_index(ann_path)
    result = crop_for_domain(raw, annotations, images, 100, bbox_padding=0.0)
    assert result is not None
    crop, info = result
    assert crop.size == (20, 20)
    assert info["domain_id"] == 100
    assert info["image_id"] == 1
    assert tuple(info["bbox"]) == (10, 10, 20, 20)


def test_crop_for_domain_with_padding(tmp_path):
    raw, ann_path = _write_fixture(tmp_path)
    annotations, images = load_annotations_index(ann_path)
    result = crop_for_domain(raw, annotations, images, 100, bbox_padding=0.2)
    assert result is not None
    crop, info = result
    # 20 * 1.4 = 28 (with 20% pad each side).
    assert crop.size == (28, 28)


def test_crop_for_domain_clamps_to_image_bounds(tmp_path):
    raw, ann_path = _write_fixture(tmp_path)
    annotations, images = load_annotations_index(ann_path)
    # Domain 101's bbox extends past the 64×64 image; crop must clamp.
    result = crop_for_domain(raw, annotations, images, 101, bbox_padding=0.5)
    assert result is not None
    crop, _ = result
    assert crop.size[0] <= 64
    assert crop.size[1] <= 64


def test_crop_for_domain_unknown_id_returns_none(tmp_path):
    raw, ann_path = _write_fixture(tmp_path)
    annotations, images = load_annotations_index(ann_path)
    assert crop_for_domain(raw, annotations, images, 9999) is None


def test_crop_for_domain_missing_raw_image(tmp_path):
    raw, ann_path = _write_fixture(tmp_path)
    (raw / "ix000_iy001.png").unlink()
    annotations, images = load_annotations_index(ann_path)
    assert crop_for_domain(raw, annotations, images, 100) is None


# ─── v0.1.4: segmentation mask + boundary overlay ───────────────────────

def _encode_rect_rle(height: int, width: int, x: int, y: int, w: int, h: int) -> dict:
    """Build a compressed COCO RLE mask with a filled rectangle."""
    full = np.zeros((height, width), dtype=np.uint8, order="F")
    full[y:y + h, x:x + w] = 1
    rle = mask_util.encode(full)
    # Always return str counts to mirror real COCO json (bytes when read
    # back by pycocotools, str when persisted to JSON).
    rle["counts"] = rle["counts"].decode("ascii")
    return rle


def test_decode_segmentation_mask_compressed_rle():
    rle = _encode_rect_rle(32, 32, 4, 6, 10, 12)
    mask = decode_segmentation_mask(rle)
    assert mask is not None
    assert mask.shape == (32, 32)
    assert mask.sum() == 10 * 12
    # Verify the 1-block is in the right place.
    assert mask[6:18, 4:14].all()


def test_decode_segmentation_mask_handles_str_counts():
    rle = _encode_rect_rle(16, 16, 2, 3, 5, 4)
    # Already str via _encode helper — confirm decode tolerates that.
    assert isinstance(rle["counts"], str)
    mask = decode_segmentation_mask(rle)
    assert mask is not None
    assert mask.sum() == 5 * 4


def test_decode_segmentation_mask_none_inputs():
    assert decode_segmentation_mask(None) is None
    assert decode_segmentation_mask({}) is None
    assert decode_segmentation_mask("") is None


def test_contours_for_mask_simple_rect():
    mask = np.zeros((20, 20), dtype=np.uint8)
    mask[5:15, 6:16] = 1
    contours = contours_for_mask(mask)
    assert len(contours) == 1
    pts = contours[0]
    # Closed polygon — first point repeated at end.
    assert np.array_equal(pts[0], pts[-1])
    # Bbox of contour matches the rectangle.
    xs, ys = pts[:, 0], pts[:, 1]
    assert xs.min() == 6 and xs.max() == 15
    assert ys.min() == 5 and ys.max() == 14


def test_contours_for_mask_empty_returns_empty():
    assert contours_for_mask(np.zeros((10, 10), dtype=np.uint8)) == []
    assert contours_for_mask(np.array([], dtype=np.uint8).reshape(0, 0)) == []


def test_build_image_preview_figure_no_mask():
    img = Image.fromarray(np.zeros((30, 40, 3), dtype=np.uint8))
    fig = build_image_preview_figure(img, None, show_boundary=True)
    # Just the imshow heatmap trace, no overlays.
    assert len(fig.data) == 1


def test_build_image_preview_figure_with_boundary_adds_traces():
    img = Image.fromarray(np.zeros((30, 30, 3), dtype=np.uint8))
    mask = np.zeros((30, 30), dtype=np.uint8)
    mask[10:20, 5:25] = 1
    fig = build_image_preview_figure(img, mask, show_boundary=True)
    # imshow trace + at least one boundary scatter.
    assert len(fig.data) >= 2
    boundary = fig.data[1]
    assert boundary.mode == "lines"
    assert boundary.line.color == BOUNDARY_COLOR


def test_build_image_preview_figure_boundary_off_skips_overlay():
    img = Image.fromarray(np.zeros((30, 30, 3), dtype=np.uint8))
    mask = np.zeros((30, 30), dtype=np.uint8)
    mask[10:20, 5:25] = 1
    fig = build_image_preview_figure(img, mask, show_boundary=False)
    assert len(fig.data) == 1


def _write_fixture_with_mask(tmp: Path) -> tuple[Path, Path]:
    """Fixture with an annotation that carries a real RLE segmentation."""
    raw = tmp / "raw_images"
    raw.mkdir()
    img = Image.fromarray(
        np.full((40, 40, 3), 200, dtype=np.uint8)
    )
    img.save(raw / "img_a.png")

    rle = _encode_rect_rle(40, 40, 5, 8, 12, 10)

    ann_path = tmp / "annotations.json"
    ann_path.write_text(
        json.dumps(
            {
                "images": [
                    {"id": 7, "file_name": "img_a.png", "width": 40, "height": 40}
                ],
                "annotations": [
                    {
                        "id": 200,
                        "image_id": 7,
                        "bbox": [5, 8, 12, 10],
                        "area": 120,
                        "score": 0.95,
                        "segmentation": rle,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return raw, ann_path


def test_crop_for_domain_with_mask_returns_cropped_mask(tmp_path):
    raw, ann_path = _write_fixture_with_mask(tmp_path)
    annotations, images = load_annotations_index(ann_path)
    result = crop_for_domain_with_mask(
        raw, annotations, images, 200, bbox_padding=0.0
    )
    assert result is not None
    crop, mask, info = result
    assert crop.size == (12, 10)
    assert mask is not None
    assert mask.shape == (10, 12)
    # Whole crop is the mask region (bbox_padding=0).
    assert mask.sum() == 10 * 12
    assert info["has_mask"] is True


def test_crop_for_domain_with_mask_handles_missing_segmentation(tmp_path):
    raw, ann_path = _write_fixture(tmp_path)  # no segmentation field
    annotations, images = load_annotations_index(ann_path)
    result = crop_for_domain_with_mask(raw, annotations, images, 100)
    assert result is not None
    _crop, mask, info = result
    assert mask is None
    assert info["has_mask"] is False
