import os
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest, save_manifest


def _make_pipeline_mock(analysis: Path):
    """Returns a stub run_selector_step that writes a 4-row selection.parquet."""
    def stub(**kwargs):
        out = analysis / "03_selector"
        out.mkdir(parents=True, exist_ok=True)
        p = out / "selection.parquet"
        pd.DataFrame({
            "domain_id": [1, 2, 3, 4],
            "selected": [True, True, False, True],
        }).to_parquet(p, index=False)
        return {
            "output_path": str(p),
            "selected_count": 3,
            "total_count": 4,
            "params": {"area_min": 5.0},
            "params_hash": "sha256:abc",
        }
    return stub


@pytest.mark.asyncio
async def test_commit_no_lasso_returns_filter_count(tmp_path):
    analysis = tmp_path / "proj"
    analysis.mkdir()
    raw = tmp_path / "raw"
    raw.mkdir()
    save_manifest(
        Manifest(analysis_folder=str(analysis), raw_images_dir=str(raw)),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)
    try:
        with patch(
            "flake_analysis.api.routes.selector.run_selector_step",
            side_effect=_make_pipeline_mock(analysis),
        ):
            app = create_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.post(
                    "/api/v1/projects/local/selector/commit",
                    json={"params": {"area_min": 5.0}, "lasso_ids": None},
                )
                assert r.status_code == 200
                body = r.json()
                assert body["n_committed"] == 3
                assert body["n_filter_accepted"] == 3
                assert body["n_lasso"] == 0
                assert body["total_count"] == 4
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_commit_with_lasso_intersects(tmp_path):
    analysis = tmp_path / "proj"
    analysis.mkdir()
    raw = tmp_path / "raw"
    raw.mkdir()
    save_manifest(
        Manifest(analysis_folder=str(analysis), raw_images_dir=str(raw)),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)
    try:
        with patch(
            "flake_analysis.api.routes.selector.run_selector_step",
            side_effect=_make_pipeline_mock(analysis),
        ):
            app = create_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.post(
                    "/api/v1/projects/local/selector/commit",
                    json={"params": {"area_min": 5.0}, "lasso_ids": [2, 3]},
                )
                assert r.status_code == 200
                body = r.json()
                # filter: {1,2,4} accepted; lasso: {2,3}; intersection: {2}
                assert body["n_committed"] == 1
                assert body["n_filter_accepted"] == 3
                assert body["n_lasso"] == 2
                # Verify file actually rewritten
                df = pd.read_parquet(analysis / "03_selector" / "selection.parquet")
                rows = dict(zip(df["domain_id"].tolist(), df["selected"].tolist()))
                assert rows == {1: False, 2: True, 3: False, 4: False}
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_commit_without_domain_stats_returns_409(tmp_path):
    """RuntimeError('Domain Stats step not completed') -> 409 prerequisite_missing."""
    analysis = tmp_path / "proj"
    analysis.mkdir()
    raw = tmp_path / "raw"
    raw.mkdir()
    save_manifest(
        Manifest(analysis_folder=str(analysis), raw_images_dir=str(raw)),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)

    def boom(**_kw):
        raise RuntimeError("Domain Stats step not completed. Run Compute → Domain Stats first.")

    try:
        with patch(
            "flake_analysis.api.routes.selector.run_selector_step",
            side_effect=boom,
        ):
            app = create_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                r = await client.post(
                    "/api/v1/projects/local/selector/commit",
                    json={"params": {"area_min": 5.0}, "lasso_ids": None},
                )
                assert r.status_code == 409
                body = r.json()
                assert body["error"]["code"] == "prerequisite_missing"
                assert "Domain Stats" in body["error"]["details"]["reason"]
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)
