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


@pytest.mark.asyncio
async def test_project_unique_owner_name(pg_session, sample_user_factory):
    """(owner_id, name) must be unique — same owner cannot have two projects with the same name."""
    owner = await sample_user_factory()
    pg_session.add(Project(name="dup", owner_id=owner.id))
    await pg_session.flush()
    pg_session.add(Project(name="dup", owner_id=owner.id))
    with pytest.raises(IntegrityError):
        await pg_session.flush()


@pytest.mark.asyncio
async def test_scan_requires_real_project(pg_session, sample_user_factory):
    """scans.project_id is now a real FK — bogus value must fail."""
    owner = await sample_user_factory()
    bad = Scan(name="s1", material="graphene", project_id="not-a-real-project", created_by_id=owner.id)
    pg_session.add(bad)
    with pytest.raises(IntegrityError):
        await pg_session.flush()


@pytest.mark.asyncio
async def test_project_delete_restricts_when_scans_exist(pg_session, sample_user_factory):
    """D2: ON DELETE RESTRICT — deleting a project that owns scans must fail."""
    owner = await sample_user_factory()
    proj = Project(name="P-restrict", owner_id=owner.id)
    pg_session.add(proj)
    await pg_session.flush()
    scan = Scan(
        name="s1", material="graphene", project_id=proj.id,
        created_by_id=owner.id,
    )
    pg_session.add(scan)
    await pg_session.flush()

    await pg_session.delete(proj)
    with pytest.raises(IntegrityError):
        await pg_session.flush()


@pytest.mark.asyncio
async def test_project_delete_cascades_project_users(pg_session, sample_user_factory):
    """W6.4 semantics: project_users grants tear down with the project."""
    owner = await sample_user_factory()
    member = await sample_user_factory()
    proj = Project(name="P-cascade", owner_id=owner.id)
    pg_session.add(proj)
    await pg_session.flush()
    pu = ProjectUser(project_id=proj.id, user_id=member.id, project_role=ProjectRole.VIEWER)
    pg_session.add(pu)
    await pg_session.flush()

    await pg_session.delete(proj)
    await pg_session.flush()

    result = await pg_session.execute(
        select(ProjectUser).where(ProjectUser.project_id == proj.id)
    )
    assert result.scalar_one_or_none() is None
