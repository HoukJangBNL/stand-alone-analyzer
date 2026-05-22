"""W5-A schema tests: materials + tightened scans/images constraints."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from flake_analysis.db.models import Image, Material, Scan

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


@pytest.mark.asyncio
async def test_scan_rejects_missing_material(pg_session, sample_user_factory):
    """scans.material is NOT NULL — inserting with NULL must fail."""
    user = await sample_user_factory()
    bad = Scan(name="t1", material=None, created_by_id=user.id)
    pg_session.add(bad)
    with pytest.raises(IntegrityError):
        await pg_session.flush()


@pytest.mark.asyncio
async def test_scan_rejects_unknown_material(pg_session, sample_user_factory):
    """scans.material FK rejects values not in materials table."""
    user = await sample_user_factory()
    bad = Scan(name="t2", material="not-a-real-material", created_by_id=user.id)
    pg_session.add(bad)
    with pytest.raises(IntegrityError):
        await pg_session.flush()


@pytest.mark.asyncio
async def test_image_grid_uniqueness(pg_session, sample_user_factory):
    """Two images on the same scan with the same (grid_ix, grid_iy) violate UNIQUE."""
    user = await sample_user_factory()
    scan = Scan(name="t3", material="graphene", created_by_id=user.id)
    pg_session.add(scan)
    await pg_session.flush()

    a = Image(
        scan_id=scan.id, sha256="a" * 64, s3_uri="s3://b/a",
        width=10, height=10, grid_ix=0, grid_iy=0,
    )
    b = Image(
        scan_id=scan.id, sha256="b" * 64, s3_uri="s3://b/b",
        width=10, height=10, grid_ix=0, grid_iy=0,
    )
    pg_session.add_all([a, b])
    with pytest.raises(IntegrityError):
        await pg_session.flush()
