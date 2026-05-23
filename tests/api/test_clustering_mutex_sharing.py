"""Both clustering endpoints share the per-scan mutex (backend design §3.2).

Asserted shape: while one endpoint is mid-stream, a request to the *other*
endpoint on the *same* scan must return 423 (or be queued — we accept
either, but contention MUST be visible).

W10-C.4b: this file is skipped because the assertions target the
clustering router (`/run/clustering/apply_thresholds`), which is still on
the pre-W10 URL surface and still imports the legacy
`acquire_project_lock`. W10-C.4c will rewrite this test against the
per-scan grammar (`acquire_scan_lock(sid)` + per-scan URL) and unskip.
"""
import asyncio
import json
from pathlib import Path

import pandas as pd
import pytest

pytestmark = pytest.mark.skip(
    reason="rewritten for per-scan in W10-C.4c (clustering router)"
)


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

    # Body deliberately left as a stub — Task 4c will rewrite around per-scan
    # semantics: acquire_scan_lock(sid) + POST to
    # /api/v1/projects/{pid}/scans/{sid}/run/clustering/apply_thresholds.
    pytest.skip("rewritten for per-scan in W10-C.4c (clustering router)")
