# tests/api/test_data_annotation_preview.py
import io
import json
import os
from pathlib import Path

import pytest
from PIL import Image
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest, save_manifest


def _setup(tmp_path: Path) -> Path:
    analysis = tmp_path / "proj"
    analysis.mkdir()
    raw = tmp_path / "raw"
    raw.mkdir()
    Image.new("RGB", (200, 200), (10, 20, 30)).save(raw / "tile_0.png")

    ann_path = tmp_path / "annotations.json"
    ann_path.write_text(json.dumps({
        "tile_0.png": {
            "domains": [
                {"domain_id": 7, "bbox": [50, 50, 100, 100], "polygon": [[50, 50], [100, 50], [100, 100], [50, 100]]},
            ],
        }
    }))

    save_manifest(
        Manifest(
            analysis_folder=str(analysis),
            raw_images_dir=str(raw),
            annotations_path=str(ann_path),
        ),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)
    return analysis


@pytest.mark.asyncio
async def test_annotation_preview_returns_png(tmp_path):
    _setup(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/v1/projects/local/data/annotations/7/preview")
            assert r.status_code == 200
            assert r.headers["content-type"] == "image/png"
            img = Image.open(io.BytesIO(r.content))
            assert img.size == (50, 50)
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_annotation_preview_with_contour_query(tmp_path):
    _setup(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(
                "/api/v1/projects/local/data/annotations/7/preview",
                params={"with_contour": "true"},
            )
            assert r.status_code == 200
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_annotation_preview_404(tmp_path):
    _setup(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/v1/projects/local/data/annotations/9999/preview")
            assert r.status_code == 404
            assert r.json()["error"]["code"] == "domain_not_found"
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)
