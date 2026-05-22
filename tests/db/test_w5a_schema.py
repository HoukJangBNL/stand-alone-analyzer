"""W5-A schema tests: materials + tightened scans/images constraints."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from flake_analysis.db.models import Material

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_material_insert_and_lookup(pg_session):
    """Material is a simple controlled-vocabulary row keyed by name."""
    pg_session.add(Material(name="graphene"))
    await pg_session.flush()

    result = await pg_session.execute(select(Material).where(Material.name == "graphene"))
    row = result.scalar_one()
    assert row.name == "graphene"
    assert row.created_at is not None
