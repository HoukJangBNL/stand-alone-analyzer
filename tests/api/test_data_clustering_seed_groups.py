import json
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


@pytest.mark.asyncio
async def test_seed_groups_returns_empty_when_missing(tmp_path: Path):
    """Missing file is the empty-list autoload contract, not a 404."""
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/v1/projects/local/data/clustering/seed_groups")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_seed_groups_returns_list(tmp_path: Path):
    (tmp_path / "04_clustering").mkdir(parents=True)
    payload = [
        {"name": "thin", "domain_ids": [1, 2, 3]},
        {"name": "thick", "domain_ids": [4, 5]},
    ]
    (tmp_path / "04_clustering" / "seed_groups.json").write_text(json.dumps(payload))

    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/v1/projects/local/data/clustering/seed_groups")
    assert r.status_code == 200
    body = r.json()
    assert body == payload
