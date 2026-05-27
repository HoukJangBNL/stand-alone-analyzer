"""runs service smoke (PG required).

Covers `record_run_start` and `record_run_end` audit-log helpers used by
the 4 SSE pipeline step routes (background / sam / domain_stats /
domain_proximity) added in P2.6.

NOTE on fixture choice: the P2.4 plan body referenced
``active_scan["analysis"]`` but the actual ``active_scan`` fixture
(tests/api/conftest.py:83) returns the Scan ORM (not a dict). We build
Model + Analysis inline on top of the API conftest's ``active_scan`` so
this test exercises the same Scan that the route-layer fixtures use.
(``sample_analysis_factory`` is now project-aware per issue #172, so
either approach is FK-safe — this one keeps the W10 ownership story
explicit.) Same pattern as tests/db/test_analysis_status_generated.py.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from flake_analysis.api.services.runs import record_run_end, record_run_start
from flake_analysis.db.models import Analysis, Model
from flake_analysis.db.models.analysis import Run

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_run_lifecycle(pg_session, active_scan):
    # Build a Model + Analysis on the active Scan so we have a valid
    # analysis_id to attach Run rows to.
    model = Model(name="t-runs-model", base_model="sam2", s3_uri="s3://t/runs")
    pg_session.add(model)
    await pg_session.flush()
    analysis = Analysis(
        scan_id=active_scan.id,
        model_id=model.id,
        amg_params={"points_per_side": 32},
        link_distance_px=10.0,
        steps_done={},
    )
    pg_session.add(analysis)
    await pg_session.flush()
    await pg_session.refresh(analysis)

    run_id = await record_run_start(
        pg_session,
        analysis_id=analysis.id,
        step="sam",
        instance_meta={
            "instance_type": "g6e.xlarge",
            "instance_id": "i-test",
            "is_spot": True,
        },
    )
    await pg_session.flush()

    row = (
        await pg_session.execute(select(Run).where(Run.id == run_id))
    ).scalar_one()
    # Run.status is Mapped[PipelineStatus]; comparing .value is enum-safe.
    assert row.status.value == "running"
    assert row.is_spot is True
    assert row.instance_type == "g6e.xlarge"
    assert row.instance_id == "i-test"
    assert row.started_at is not None
    assert row.completed_at is None

    await record_run_end(
        pg_session,
        run_id=run_id,
        status="completed",
        metrics={"images": 2, "masks_total": 7, "errors": 0},
    )
    await pg_session.flush()

    # Re-read picks up the UPDATE; refresh busts the identity-map cache
    # so the fields reflect the post-update values rather than the
    # pre-update snapshot.
    await pg_session.refresh(row)
    assert row.status.value == "completed"
    assert row.completed_at is not None
    assert row.error is None
    assert row.metrics["images"] == 2
    assert row.metrics["masks_total"] == 7
