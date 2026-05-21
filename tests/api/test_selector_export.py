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
    pd.DataFrame({
        "domain_id": [1, 2, 3, 4],
        "selected": [True, False, True, True],
    }).to_parquet(analysis / "03_selector" / "selection.parquet", index=False)
    save_manifest(
        Manifest(analysis_folder=str(analysis), raw_images_dir=str(tmp_path / "raw")),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)
    return analysis


@pytest.mark.asyncio
async def test_export_selected_only(tmp_path):
    _setup(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(
                "/api/v1/projects/local/selector/export",
                params={"mode": "selected"},
            )
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/csv")
            lines = r.text.strip().splitlines()
            assert lines[0] == "domain_id,selected"
            assert {l for l in lines[1:]} == {"1,True", "3,True", "4,True"}
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_export_filtered_returns_all_rows(tmp_path):
    _setup(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(
                "/api/v1/projects/local/selector/export",
                params={"mode": "filtered"},
            )
            assert r.status_code == 200
            lines = r.text.strip().splitlines()
            assert len(lines) == 5  # header + 4 rows
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_export_invalid_mode(tmp_path):
    _setup(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(
                "/api/v1/projects/local/selector/export",
                params={"mode": "garbage"},
            )
            assert r.status_code == 422
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)
