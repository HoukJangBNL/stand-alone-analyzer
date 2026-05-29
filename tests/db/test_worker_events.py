"""Round-trip test for the WorkerEvent ORM model + 0007 migration."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.db.models import WorkerEvent

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_worker_event_round_trip(pg_session: AsyncSession) -> None:
    row = WorkerEvent(
        run_id=42,
        event="marker:processing_start",
        payload={"weights": "merged_m3", "n_gpus": 8},
    )
    pg_session.add(row)
    await pg_session.flush()
    await pg_session.refresh(row)

    assert row.id is not None
    assert row.ts is not None  # server_default NOW()
    assert row.event == "marker:processing_start"
    assert row.payload == {"weights": "merged_m3", "n_gpus": 8}

    fetched = (await pg_session.execute(
        select(WorkerEvent).where(WorkerEvent.run_id == 42)
    )).scalar_one()
    assert fetched.id == row.id


@pytest.mark.asyncio
async def test_worker_event_payload_optional(pg_session: AsyncSession) -> None:
    row = WorkerEvent(run_id=1, event="marker:model_load_start", payload=None)
    pg_session.add(row)
    await pg_session.flush()
    assert row.payload is None
