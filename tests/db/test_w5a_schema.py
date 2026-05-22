"""W5-A schema tests: materials + tightened scans/images constraints."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from flake_analysis.db.models import Material

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_material_insert_and_lookup(pg_session):
    """Material is a simple controlled-vocabulary row keyed by name.

    Use a name NOT in the migration seed so the per-test rollback fixture
    can insert cleanly without colliding with seeded rows.
    """
    pg_session.add(Material(name="WTe2"))
    await pg_session.flush()

    result = await pg_session.execute(select(Material).where(Material.name == "WTe2"))
    row = result.scalar_one()
    assert row.name == "WTe2"
    assert row.created_at is not None


@pytest.mark.asyncio
async def test_seed_materials_present(pg_session):
    """Migration seeds the 5 baseline materials."""
    result = await pg_session.execute(select(Material.name))
    names = set(result.scalars().all())
    for expected in {"graphene", "MoS2", "WSe2", "hBN", "WS2"}:
        assert expected in names
