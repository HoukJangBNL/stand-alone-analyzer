# tests/api/test_run_thumbnails_sse.py
import pytest
import json
import os
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport
from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest, save_manifest

@pytest.mark.asyncio
async def test_run_thumbnails_sse(tmp_path):
    """POST /run/thumbnails streams progress and completes."""
    analysis_folder = tmp_path / "proj"
    analysis_folder.mkdir()
    raw_images_dir = tmp_path / "raw"
    raw_images_dir.mkdir()

    m = Manifest(
        analysis_folder=str(analysis_folder),
        raw_images_dir=str(raw_images_dir),
    )
    save_manifest(m, analysis_folder)

    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis_folder)

    def mock_run_thumbnails(**kwargs):
        cb = kwargs.get("progress_callback")
        if cb:
            cb(0.0, "start")
            cb(0.5, "halfway")
            cb(1.0, "done")
        return {
            "output_dir": str(analysis_folder / "00_thumbnails"),
            "n_images": 10,
            "n_skipped": 0,
            "n_failed": 0,
            "params": {"quality": 80},
            "params_hash": "sha256:abc",
            "cache_dir": None,
        }

    with patch("flake_analysis.api.routes.run.run_thumbnails_step", side_effect=mock_run_thumbnails):
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            async with client.stream("POST", "/api/v1/projects/local/run/thumbnails", json={"quality": 80}) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers["content-type"]

                events = []
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        events.append(data)

                progress_events = [e for e in events if e["type"] == "progress"]
                done_events = [e for e in events if e["type"] == "done"]

                assert len(progress_events) == 3
                assert len(done_events) == 1
                assert done_events[0]["result"]["n_images"] == 10

    os.environ.pop("SAA_ANALYSIS_FOLDER", None)
