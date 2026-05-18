"""Smoke tests for ``flake_analysis.ui._image_preview`` (PR v0.1.2).

Exercises the pure-Python helpers (no Streamlit boot):
  * load_annotations_index — direct json.loads fallback
  * crop_for_domain — bbox + padding + clamp
"""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from flake_analysis.ui._image_preview import (
    crop_for_domain,
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
