# tests/api/test_data_selection.py
import os
from pathlib import Path

import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest, save_manifest


def _setup(tmp_path: Path) -> Path:
    analysis = tmp_path / "proj"
    analysis.mkdir()
    (analysis / "03_selector").mkdir()
    df = pd.DataFrame({
        "domain_id": [1, 2, 3, 4],
        "selected": [True, False, True, True],
    })
    df.to_parquet(analysis / "03_selector" / "selection.parquet", index=False)

    save_manifest(
        Manifest(analysis_folder=str(analysis), raw_images_dir=str(tmp_path / "raw")),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)
    return analysis


@pytest.mark.asyncio
async def test_selection_json(tmp_path):
    _setup(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/v1/projects/local/data/selector/selection")
            assert r.status_code == 200
            payload = r.json()
            assert payload["domain_id"] == [1, 2, 3, 4]
            assert payload["selected"] == [True, False, True, True]
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_selection_404_when_missing(tmp_path):
    analysis = tmp_path / "proj"
    analysis.mkdir()
    save_manifest(
        Manifest(analysis_folder=str(analysis), raw_images_dir=str(tmp_path / "raw")),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/v1/projects/local/data/selector/selection")
            assert r.status_code == 404
            assert r.json()["error"]["code"] == "selection_not_found"
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)
