import pytest
from sqlalchemy import Column, Integer, MetaData, Table

from scripts.check_alembic_drift import compute_drift


@pytest.mark.pg
@pytest.mark.asyncio
async def test_compute_drift_clean(pg_engine):
    """A schema that exactly matches Base.metadata returns []."""
    from flake_analysis.db import Base
    async with pg_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    diffs = await compute_drift(pg_engine, Base.metadata)
    assert diffs == []


@pytest.mark.pg
@pytest.mark.asyncio
async def test_compute_drift_extra_db_table(pg_engine):
    """An extra table in the DB but not in metadata is flagged."""
    from flake_analysis.db import Base
    async with pg_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("CREATE TABLE rogue (id INTEGER PRIMARY KEY)")

    diffs = await compute_drift(pg_engine, Base.metadata)
    assert any("rogue" in str(op) for op in diffs)
