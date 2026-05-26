"""Scan-level access guards (W11).

These helpers translate the existing project-level ACL into scan-level
checks: load the scan, look up its parent project, resolve the caller's
effective ProjectRole, and raise ScanNotFound (404) for outsiders or
Forbidden (403) for viewers attempting writes.

Why ScanNotFound and not Forbidden for outsiders: returning 403 would leak
the existence of scans in projects the caller has no business knowing
about. 403 is reserved for "you ARE in the project, but your role is too
low for this action".
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api import errors as app_errors
from flake_analysis.api.auth import User
from flake_analysis.api.services.acl import resolve_effective_project_role
from flake_analysis.db.models import Project, ProjectRole, ProjectUser, Scan


async def _resolve_effective(
    session: AsyncSession, *, scan_id: int, user: User,
) -> tuple[Scan, ProjectRole | None]:
    scan = (
        await session.execute(select(Scan).where(Scan.id == scan_id))
    ).scalar_one_or_none()
    if scan is None:
        raise app_errors.ScanNotFound(scan_id=scan_id)

    project = (
        await session.execute(select(Project).where(Project.id == scan.project_id))
    ).scalar_one()

    is_owner = project.owner_id == user.id
    acl_role = (
        await session.execute(
            select(ProjectUser.project_role)
            .where(ProjectUser.project_id == scan.project_id)
            .where(ProjectUser.user_id == user.id)
        )
    ).scalar_one_or_none()

    effective = resolve_effective_project_role(
        user.role, is_owner=is_owner, acl_role=acl_role,
    )
    return scan, effective


async def get_scan_for_user(
    session: AsyncSession, *, scan_id: int, user: User,
) -> Scan:
    """Return the scan iff caller has any access to the parent project; else 404."""
    scan, effective = await _resolve_effective(session, scan_id=scan_id, user=user)
    if effective is None:
        raise app_errors.ScanNotFound(scan_id=scan_id)
    return scan


async def require_editor_for_scan(
    session: AsyncSession, *, scan_id: int, user: User,
) -> Scan:
    """Like get_scan_for_user but additionally requires editor role; else 403."""
    scan, effective = await _resolve_effective(session, scan_id=scan_id, user=user)
    if effective is None:
        raise app_errors.ScanNotFound(scan_id=scan_id)
    if effective != ProjectRole.EDITOR:
        raise app_errors.Forbidden(action="scan_edit", scan_id=scan_id)
    return scan
