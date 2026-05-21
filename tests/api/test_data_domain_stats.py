# tests/api/test_data_domain_stats.py
import io
import json
import os
from pathlib import Path

import numpy as np
import pyarrow.ipc as ipc
import pytest
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest, save_manifest


def _setup_project(tmp_path: Path) -> Path:
    analysis = tmp_path / "proj"
    analysis.mkdir()
    raw = tmp_path / "raw"
    raw.mkdir()
    stats_dir = analysis / "02_domain_stats"
    stats_dir.mkdir()
    np.savez(
        stats_dir / "stats.npz",
        flake_ids=np.array([1, 2, 3], dtype=np.int64),
        repr_rgbs=np.array([[10, 20, 30], [40, 50, 60], [70, 80, 90]], dtype=np.float64),
        std_pcts=np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]], dtype=np.float64),
        areas=np.array([100.0, 200.0, 300.0], dtype=np.float64),
        sam2=np.array([0.1, 0.5, 0.9], dtype=np.float64),
    )
    save_manifest(
        Manifest(analysis_folder=str(analysis), raw_images_dir=str(raw)),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)
    return analysis


@pytest.mark.asyncio
async def test_data_domain_stats_json(tmp_path):
    _setup_project(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/v1/projects/local/data/domain_stats")
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("application/json")
            payload = r.json()
            assert payload["flake_ids"] == [1, 2, 3]
            assert payload["areas"] == [100.0, 200.0, 300.0]
            assert payload["sam2"] == [0.1, 0.5, 0.9]
            # std_pcts split into 3 columns for typed-array consumption
            assert payload["std_r"] == [1.0, 4.0, 7.0]
            assert payload["std_g"] == [2.0, 5.0, 8.0]
            assert payload["std_b"] == [3.0, 6.0, 9.0]
            # repr_rgbs likewise
            assert payload["mean_r"] == [10.0, 40.0, 70.0]
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_data_domain_stats_arrow(tmp_path):
    _setup_project(tmp_path)
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get(
                "/api/v1/projects/local/data/domain_stats",
                headers={"Accept": "application/vnd.apache.arrow.stream"},
            )
            assert r.status_code == 200
            assert r.headers["content-type"] == "application/vnd.apache.arrow.stream"
            reader = ipc.open_stream(io.BytesIO(r.content))
            table = reader.read_all()
            assert table.num_rows == 3
            assert table.column("flake_ids").to_pylist() == [1, 2, 3]
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_data_domain_stats_missing_npz(tmp_path):
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
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.get("/api/v1/projects/local/data/domain_stats")
            assert r.status_code == 404
            body = r.json()
            assert body["error"]["code"] == "domain_stats_not_found"
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)
