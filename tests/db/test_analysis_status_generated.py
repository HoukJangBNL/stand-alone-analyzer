"""Round-trip tests for the GENERATED analyses.status column.

The ORM declares analyses.status as Computed(..., persisted=True) +
FetchedValue() so that INSERT ... RETURNING (and explicit refresh) pull
the value PostgreSQL actually computed from steps_done. Without
Computed(...), some SQLAlchemy paths fall back to ORM-side defaults and
the Python attribute disagrees with the database.

Each test creates one Scan + Model + Analysis row and rolls back.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.db.models import (
    Analysis,
    Model,
    PipelineStatus,
    Project,
    Scan,
    User,
)

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def _seed_scan_and_model(session: AsyncSession) -> tuple[int, int]:
    # W10-A made scans.project_id NOT NULL FK→projects.id, so seed a User +
    # Project per call. `material` is NOT NULL + FK→materials(name); use the
    # seeded "graphene" row.
    suffix = uuid4().hex[:8]
    user = User(
        cognito_sub=f"test-cognito-{suffix}",
        email=f"test-{suffix}@example.com",
    )
    session.add(user)
    await session.flush()
    project = Project(name=f"test-project-{suffix}", owner_id=user.id)
    session.add(project)
    await session.flush()
    scan = Scan(
        name="t-scan", material="graphene", image_count=0,
        project_id=project.id,
    )
    model = Model(name="t-model", base_model="sam2", s3_uri="s3://x/y")
    session.add_all([scan, model])
    await session.flush()
    return scan.id, model.id


async def _make_analysis(
    session: AsyncSession,
    *,
    scan_id: int,
    model_id: int,
    steps_done: dict,
) -> Analysis:
    a = Analysis(
        scan_id=scan_id,
        model_id=model_id,
        amg_params={"points_per_side": 32},
        link_distance_px=10.0,
        steps_done=steps_done,
    )
    session.add(a)
    await session.flush()
    await session.refresh(a)
    return a


@pytest.mark.parametrize(
    "steps_done, expected",
    [
        ({}, PipelineStatus.PENDING),
        ({"background": True}, PipelineStatus.RUNNING),
        ({"background": True, "sam": True}, PipelineStatus.RUNNING),
        (
            {"background": True, "sam": True, "domain_stats": True, "domain_proximity": True},
            PipelineStatus.COMPLETED,
        ),
        ({"failed": "background timeout"}, PipelineStatus.FAILED),
        # 'failed' wins over a partial-completion shape.
        ({"background": True, "failed": "sam crashed"}, PipelineStatus.FAILED),
        # domain_proximity present but falsy → still 'running', not 'completed'.
        ({"background": True, "domain_proximity": False}, PipelineStatus.RUNNING),
    ],
)
async def test_status_reflects_steps_done_after_insert(
    pg_session: AsyncSession, steps_done: dict, expected: PipelineStatus
) -> None:
    scan_id, model_id = await _seed_scan_and_model(pg_session)
    a = await _make_analysis(
        pg_session, scan_id=scan_id, model_id=model_id, steps_done=steps_done
    )
    assert a.status == expected, (
        f"steps_done={steps_done!r} → expected {expected!r}, got {a.status!r}"
    )


async def test_status_updates_after_steps_done_mutation(
    pg_session: AsyncSession,
) -> None:
    scan_id, model_id = await _seed_scan_and_model(pg_session)
    a = await _make_analysis(
        pg_session, scan_id=scan_id, model_id=model_id, steps_done={}
    )
    assert a.status == PipelineStatus.PENDING

    a.steps_done = {"background": True}
    await pg_session.flush()
    await pg_session.refresh(a)
    assert a.status == PipelineStatus.RUNNING

    a.steps_done = {
        "background": True,
        "sam": True,
        "domain_stats": True,
        "domain_proximity": True,
    }
    await pg_session.flush()
    await pg_session.refresh(a)
    assert a.status == PipelineStatus.COMPLETED


async def test_status_is_read_only_via_orm(pg_session: AsyncSession) -> None:
    """Writing to .status must not be persisted (PG rejects writes to GENERATED)."""
    scan_id, model_id = await _seed_scan_and_model(pg_session)
    a = await _make_analysis(
        pg_session, scan_id=scan_id, model_id=model_id, steps_done={}
    )
    a.status = PipelineStatus.COMPLETED  # type: ignore[assignment]
    with pytest.raises(Exception):
        # PG raises on UPDATE of a GENERATED column; SQLAlchemy surfaces it.
        await pg_session.flush()
