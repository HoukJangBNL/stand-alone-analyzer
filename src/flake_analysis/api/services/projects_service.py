"""DB-side helpers for projects CRUD (W10-C)."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.db.models import Project, Scan


class DuplicateProjectName(Exception):
    """Raised when (owner_id, name) UNIQUE violation fires."""


class ProjectNotFound(Exception):
    """Raised when a project_id has no row."""


class ProjectHasScans(Exception):
    """Raised when DELETE is attempted on a project with at least 1 scan (D2)."""

    def __init__(self, project_id: str, scan_count: int):
        self.project_id = project_id
        self.scan_count = scan_count
        super().__init__(f"project {project_id!r} has {scan_count} scan(s)")


async def create_project(
    session: AsyncSession,
    *,
    owner_id: UUID,
    name: str,
    description: str | None = None,
) -> Project:
    p = Project(name=name, owner_id=owner_id, description=description)
    session.add(p)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateProjectName(name) from exc
    return p


async def list_projects_for_user(
    session: AsyncSession, *, user_id: UUID
) -> list[Project]:
    """v1: returns projects the user owns. v2 will union with project_users grants."""
    result = await session.execute(
        select(Project)
        .where(Project.owner_id == user_id)
        .order_by(Project.created_at.desc())
    )
    return list(result.scalars().all())


async def get_project(session: AsyncSession, *, project_id: str) -> Project:
    p = (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()
    if p is None:
        raise ProjectNotFound(project_id)
    return p


async def get_project_with_scan_count(
    session: AsyncSession, *, project_id: str
) -> tuple[Project, int]:
    p = await get_project(session, project_id=project_id)
    n = (
        await session.execute(
            select(func.count(Scan.id)).where(Scan.project_id == project_id)
        )
    ).scalar_one()
    return p, int(n)


async def patch_project(
    session: AsyncSession,
    *,
    project_id: str,
    name: str | None = None,
    description: str | None = None,
) -> Project:
    p = await get_project(session, project_id=project_id)
    if name is not None:
        p.name = name
    if description is not None:
        p.description = description
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateProjectName(name or "") from exc
    return p


async def delete_project_or_409(
    session: AsyncSession, *, project_id: str
) -> None:
    """Delete iff no scans exist; otherwise raise ProjectHasScans (D2)."""
    p = await get_project(session, project_id=project_id)
    n = (
        await session.execute(
            select(func.count(Scan.id)).where(Scan.project_id == project_id)
        )
    ).scalar_one()
    if n > 0:
        raise ProjectHasScans(project_id, int(n))
    await session.delete(p)
    await session.flush()
