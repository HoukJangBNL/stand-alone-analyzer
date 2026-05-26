"""W10-C: projects_service unit tests (PG-backed)."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from flake_analysis.api.services import projects_service
from flake_analysis.db.models import Project, Scan

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_create_project_assigns_uuid_id(pg_session, sample_user_factory):
    owner = await sample_user_factory()
    p = await projects_service.create_project(
        pg_session, owner_id=owner.id, name="P1", description="hello",
    )
    await pg_session.flush()
    assert p.id and len(p.id) >= 8
    assert p.name == "P1"
    assert p.description == "hello"
    assert p.owner_id == owner.id


@pytest.mark.asyncio
async def test_create_project_rejects_dup_name_per_owner(pg_session, sample_user_factory):
    owner = await sample_user_factory()
    await projects_service.create_project(pg_session, owner_id=owner.id, name="dup")
    await pg_session.flush()
    with pytest.raises(projects_service.DuplicateProjectName):
        await projects_service.create_project(pg_session, owner_id=owner.id, name="dup")
        await pg_session.flush()


@pytest.mark.asyncio
async def test_list_projects_for_user_returns_owned_only(pg_session, sample_user_factory):
    a = await sample_user_factory()
    b = await sample_user_factory()
    await projects_service.create_project(pg_session, owner_id=a.id, name="A1")
    await projects_service.create_project(pg_session, owner_id=a.id, name="A2")
    await projects_service.create_project(pg_session, owner_id=b.id, name="B1")
    await pg_session.flush()

    rows = await projects_service.list_projects_for_user(pg_session, user_id=a.id)
    names = {r.name for r in rows}
    assert names == {"A1", "A2"}


@pytest.mark.asyncio
async def test_delete_project_restricts_when_scans_exist(pg_session, sample_user_factory):
    owner = await sample_user_factory()
    p = await projects_service.create_project(pg_session, owner_id=owner.id, name="restrict-me")
    await pg_session.flush()
    pg_session.add(Scan(name="s1", material="graphene", project_id=p.id, created_by_id=owner.id))
    await pg_session.flush()

    with pytest.raises(projects_service.ProjectHasScans) as exc_info:
        await projects_service.delete_project_or_409(pg_session, project_id=p.id)
    assert exc_info.value.scan_count == 1


@pytest.mark.asyncio
async def test_delete_project_succeeds_when_empty(pg_session, sample_user_factory):
    owner = await sample_user_factory()
    p = await projects_service.create_project(pg_session, owner_id=owner.id, name="empty")
    await pg_session.flush()

    await projects_service.delete_project_or_409(pg_session, project_id=p.id)
    await pg_session.flush()
    gone = (await pg_session.execute(select(Project).where(Project.id == p.id))).scalar_one_or_none()
    assert gone is None


# --- W11 T2: get_project_for_user ---


@pytest.mark.asyncio
async def test_get_project_for_user_owner_ok(pg_session, sample_user_factory, sample_project_factory):
    from flake_analysis.api.auth import User as DomainUser
    from flake_analysis.db.models import UserRole

    owner_orm = await sample_user_factory(role=UserRole.MEMBER)
    project = await sample_project_factory(owner=owner_orm)
    owner = DomainUser(
        id=owner_orm.id,
        email=owner_orm.email or "",
        role=owner_orm.role,
        email_verified=True,
        cognito_sub=owner_orm.cognito_sub or "",
    )
    got = await projects_service.get_project_for_user(
        pg_session, project_id=project.id, user=owner,
    )
    assert got.id == project.id


@pytest.mark.asyncio
async def test_get_project_for_user_outsider_404(pg_session, sample_user_factory, sample_project_factory):
    from flake_analysis.api import errors as app_errors
    from flake_analysis.api.auth import User as DomainUser
    from flake_analysis.db.models import UserRole

    owner_orm = await sample_user_factory(role=UserRole.MEMBER)
    outsider_orm = await sample_user_factory(role=UserRole.MEMBER)
    project = await sample_project_factory(owner=owner_orm)
    outsider = DomainUser(
        id=outsider_orm.id,
        email=outsider_orm.email or "",
        role=outsider_orm.role,
        email_verified=True,
        cognito_sub=outsider_orm.cognito_sub or "",
    )
    with pytest.raises(app_errors.ProjectNotFound):
        await projects_service.get_project_for_user(
            pg_session, project_id=project.id, user=outsider,
        )


@pytest.mark.asyncio
async def test_get_project_for_user_reader_403_when_editor_required(
    pg_session, sample_user_factory, sample_project_factory,
):
    from flake_analysis.api import errors as app_errors
    from flake_analysis.api.auth import User as DomainUser
    from flake_analysis.db.models import UserRole

    owner_orm = await sample_user_factory(role=UserRole.MEMBER)
    reader_orm = await sample_user_factory(role=UserRole.READER)
    project = await sample_project_factory(owner=owner_orm)
    reader = DomainUser(
        id=reader_orm.id,
        email=reader_orm.email or "",
        role=reader_orm.role,
        email_verified=True,
        cognito_sub=reader_orm.cognito_sub or "",
    )
    with pytest.raises(app_errors.Forbidden):
        await projects_service.get_project_for_user(
            pg_session, project_id=project.id, user=reader, require_editor=True,
        )


@pytest.mark.asyncio
async def test_get_project_for_user_admin_always_editor(
    pg_session, sample_user_factory, sample_project_factory,
):
    from flake_analysis.api.auth import User as DomainUser
    from flake_analysis.db.models import UserRole

    owner_orm = await sample_user_factory(role=UserRole.MEMBER)
    admin_orm = await sample_user_factory(role=UserRole.ADMIN)
    project = await sample_project_factory(owner=owner_orm)
    admin = DomainUser(
        id=admin_orm.id,
        email=admin_orm.email or "",
        role=admin_orm.role,
        email_verified=True,
        cognito_sub=admin_orm.cognito_sub or "",
    )
    got = await projects_service.get_project_for_user(
        pg_session, project_id=project.id, user=admin, require_editor=True,
    )
    assert got.id == project.id
