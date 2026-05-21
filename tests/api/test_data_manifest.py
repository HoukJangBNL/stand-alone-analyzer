import pytest
import os
from httpx import AsyncClient, ASGITransport
from flake_analysis.api.deps import get_active_analysis
from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest, save_manifest, StepEntry
from datetime import datetime, timezone

@pytest.mark.asyncio
async def test_get_manifest(tmp_path):
    """GET /projects/{pid}/data/manifest returns ManifestModel.

    W2.4: route now also depends on ``get_active_analysis``. We override it
    to ``None`` here so this disk-path test does not require a live DB.
    """
    analysis_folder = tmp_path / "proj"
    analysis_folder.mkdir()

    m = Manifest(analysis_folder=str(analysis_folder))
    m.steps["thumbnails"] = StepEntry(
        completed_at=datetime.now(timezone.utc).isoformat(),
        params={"quality": 80},
        params_hash="sha256:abc",
    )
    save_manifest(m, analysis_folder)

    os.environ["SAA_ANALYSIS_FOLDER"] = str(analysis_folder)

    app = create_app()
    app.dependency_overrides[get_active_analysis] = lambda: None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/projects/local/data/manifest")
            assert resp.status_code == 200
            body = resp.json()
            assert body["version"] == 1
            assert "thumbnails" in body["steps"]
            assert body["steps"]["thumbnails"]["params"]["quality"] == 80
    finally:
        app.dependency_overrides.clear()

    os.environ.pop("SAA_ANALYSIS_FOLDER", None)
