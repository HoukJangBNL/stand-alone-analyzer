import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from flake_analysis.api.services.annotation_preview import load_preview


@pytest.fixture
def fake_project(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    img = Image.new("RGB", (200, 200), color=(40, 80, 120))
    img.save(raw / "tile_0.png")

    annotations = {
        "tile_0.png": {
            "domains": [
                {"domain_id": 7, "bbox": [50, 50, 100, 100], "polygon": [[50, 50], [100, 50], [100, 100], [50, 100]]},
            ],
        }
    }
    ann_path = tmp_path / "annotations.json"
    ann_path.write_text(json.dumps(annotations))

    return raw, ann_path


def test_load_preview_returns_png_bytes(fake_project):
    raw, ann_path = fake_project
    png = load_preview(
        annotations_path=ann_path,
        raw_images_dir=raw,
        domain_id=7,
        with_contour=False,
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(png))
    assert img.size == (50, 50)


def test_load_preview_with_contour_returns_png(fake_project):
    raw, ann_path = fake_project
    png = load_preview(
        annotations_path=ann_path,
        raw_images_dir=raw,
        domain_id=7,
        with_contour=True,
    )
    img = Image.open(io.BytesIO(png))
    arr = np.array(img)
    # Contour pixels (red) should be present somewhere along the bbox edge.
    red = (arr[..., 0] > 200) & (arr[..., 1] < 80) & (arr[..., 2] < 80)
    assert red.any()


def test_load_preview_unknown_domain_raises(fake_project):
    raw, ann_path = fake_project
    with pytest.raises(KeyError):
        load_preview(
            annotations_path=ann_path,
            raw_images_dir=raw,
            domain_id=999,
            with_contour=False,
        )
