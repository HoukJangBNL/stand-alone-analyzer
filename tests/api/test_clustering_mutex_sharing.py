"""Both clustering endpoints share the per-project mutex (backend design §3.2).

Asserted shape: while one endpoint is mid-stream, a request to the *other*
endpoint on the *same* project must return 423 (or be queued — we accept
either, but contention MUST be visible).
"""
import asyncio
import json
from pathlib import Path

import pandas as pd
import pytest
from httpx import AsyncClient, ASGITransport

from flake_analysis.api.main import create_app
from flake_analysis.api.mutex import acquire_project_lock
from flake_analysis.state.manifest import Manifest


@pytest.mark.asyncio
async def test_apply_thresholds_blocks_while_refit_holds_lock(tmp_path: Path):
    # Write minimal clustering artifacts so apply_thresholds reaches its work, not its prereq guard.
    (tmp_path / "04_clustering").mkdir(parents=True)
    pd.DataFrame({
        "domain_id": [1, 2],
        "cluster_label": [0, 1],
        "max_posterior": [0.9, 0.8],
    }).to_parquet(tmp_path / "04_clustering" / "assignments.parquet", index=False)
    (tmp_path / "04_clustering" / "labels.json").write_text(json.dumps({
        "version": 1, "n_clusters": 2,
        "groups": [
            {"id": 0, "name": "a", "size": 1, "mean_rgb": [0, 0, 0]},
            {"id": 1, "name": "b", "size": 1, "mean_rgb": [0, 0, 0]},
        ],
        "assignments": {"1": 0, "2": 1},
        "thresholds": {"0": 0.5, "1": 0.5},
        "noise_label": -1, "random_state": 42, "fitted_at": "2026-05-21T00:00:00Z",
    }))
    (tmp_path / "manifest.json").write_text(json.dumps({"version": 1, "steps": {}}))

    app = create_app()
    manifest = Manifest(analysis_folder=str(tmp_path))
    from flake_analysis.api import deps
    app.dependency_overrides[deps.get_manifest] = lambda project_id="local": manifest

    # Manually grab the lock OUTSIDE the route, to simulate refit holding it.
    held = acquire_project_lock("local")
    await held.__aenter__()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # The route should signal contention either as 423 immediately, or
            # by waiting; we run with a short timeout to detect waiting.
            async def _send_and_drain():
                async with ac.stream(
                    "POST",
                    "/api/v1/projects/local/run/clustering/apply_thresholds",
                    json={"cluster_thresholds": {0: 0.5, 1: 0.5}},
                ) as r:
                    status = r.status_code
                    if status == 423:
                        return status  # contention surfaced as 423 — fine
                    # If the route waits on the lock, the timeout will fire below.
                    async for _ in r.aiter_text():
                        pass
                    return status

            try:
                status = await asyncio.wait_for(_send_and_drain(), timeout=0.5)
            except asyncio.TimeoutError:
                # Route is waiting on the lock — that's the "queued" branch. Acceptable.
                return

            if status == 423:
                return  # contention surfaced as 423 — fine
            # If we got here without 423 and without timeout, something is wrong.
            pytest.fail("apply_thresholds should have signalled contention while lock is held")
    finally:
        await held.__aexit__(None, None, None)
