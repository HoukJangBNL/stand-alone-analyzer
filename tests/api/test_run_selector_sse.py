import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest, save_manifest


@pytest.mark.asyncio
async def test_run_selector_sse_streams_progress_and_done(tmp_path):
    analysis = tmp_path / "proj"
    analysis.mkdir()
    raw = tmp_path / "raw"
    raw.mkdir()
    save_manifest(
        Manifest(analysis_folder=str(analysis), raw_images_dir=str(raw)),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)

    def mock_run_selector(**kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            cb(0.0, "loading")
            cb(0.5, "filtering")
            cb(1.0, "done")
        return {
            "output_path": str(analysis / "03_selector" / "selection.parquet"),
            "selected_count": 7,
            "total_count": 12,
            "params": {"area_min": 5.0},
            "params_hash": "sha256:zzz",
        }

    try:
        with patch(
            "flake_analysis.api.routes.selector.run_selector_step",
            side_effect=mock_run_selector,
        ):
            app = create_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                async with client.stream(
                    "POST",
                    "/api/v1/projects/local/run/selector",
                    json={"area_min": 5.0},
                ) as resp:
                    assert resp.status_code == 200
                    assert "text/event-stream" in resp.headers["content-type"]
                    events = []
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            events.append(json.loads(line[6:]))
                    progress = [e for e in events if e["type"] == "progress"]
                    done = [e for e in events if e["type"] == "done"]
                    assert len(progress) == 3
                    assert len(done) == 1
                    assert done[0]["result"]["selected_count"] == 7
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)


@pytest.mark.asyncio
async def test_run_selector_propagates_pipeline_error(tmp_path):
    analysis = tmp_path / "proj"
    analysis.mkdir()
    raw = tmp_path / "raw"
    raw.mkdir()
    save_manifest(
        Manifest(analysis_folder=str(analysis), raw_images_dir=str(raw)),
        analysis,
    )
    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis)

    def boom(**_kwargs):
        raise RuntimeError("Domain Stats step not completed.")

    try:
        with patch(
            "flake_analysis.api.routes.selector.run_selector_step",
            side_effect=boom,
        ):
            app = create_app()
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                async with client.stream(
                    "POST",
                    "/api/v1/projects/local/run/selector",
                    json={},
                ) as resp:
                    events = []
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            events.append(json.loads(line[6:]))
                    err = [e for e in events if e["type"] == "error"]
                    assert len(err) == 1
                    assert err[0]["error"]["code"] == "pipeline_failed"
                    assert "Domain Stats" in err[0]["error"]["message"]
    finally:
        os.environ.pop("SAA_ANALYSIS_FOLDER", None)
