"""SSE refit route — error path + lock-release semantics.

The happy path is exercised end-to-end via tests/api/test_clustering_mutex_sharing.py;
this file focuses on the contract surface (route exists, params validate, lock releases).
"""
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.state.manifest import Manifest


@pytest.mark.asyncio
async def test_refit_streams_error_when_prereq_missing(tmp_path: Path):
    """No domain_stats / selector commit → wrapper raises RuntimeError → SSE 'error' event."""
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    body = {
        "seed_groups": [
            {"name": "a", "domain_ids": [1, 2]},
            {"name": "b", "domain_ids": [3, 4]},
        ],
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream(
            "POST", "/api/v1/projects/local/run/clustering/refit", json=body
        ) as r:
            assert r.status_code == 200  # SSE convention
            text = ""
            async for chunk in r.aiter_text():
                text += chunk
            assert 'event: error' in text or '"type": "error"' in text or '"type":"error"' in text


@pytest.mark.asyncio
async def test_refit_releases_lock_after_error(tmp_path: Path):
    """After the first request errors out, the project mutex must be free for the next request."""
    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    body = {"seed_groups": [{"name": "a", "domain_ids": [1, 2]}, {"name": "b", "domain_ids": [3]}]}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # First call: drains
        async with ac.stream(
            "POST", "/api/v1/projects/local/run/clustering/refit", json=body
        ) as r1:
            async for _ in r1.aiter_text():
                pass
        # Second call: must NOT 423 (lock should be released)
        async with ac.stream(
            "POST", "/api/v1/projects/local/run/clustering/refit", json=body
        ) as r2:
            assert r2.status_code != 423
            async for _ in r2.aiter_text():
                pass
