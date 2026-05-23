"""W10-A schema tests: projects table + scans/project_users FK rewiring."""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from flake_analysis.db.models import Project, ProjectUser, Scan, User
from flake_analysis.db.models.auth import ProjectRole

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_project_insert_and_lookup(pg_session, sample_user_factory):
    """Project is a simple keyed row with name + owner_id FK."""
    owner = await sample_user_factory()
    p = Project(name="P1", owner_id=owner.id)
    pg_session.add(p)
    await pg_session.flush()

    assert isinstance(p.id, str) and len(p.id) >= 8
    assert p.created_at is not None

    result = await pg_session.execute(select(Project).where(Project.id == p.id))
    row = result.scalar_one()
    assert row.name == "P1"
    assert row.owner_id == owner.id
