"""W11 T3 — scan-level access guard tests."""
from __future__ import annotations

import pytest

from flake_analysis.api import errors as app_errors
from flake_analysis.api.auth import User as DomainUser
from flake_analysis.api.services import scans_service
from flake_analysis.db.models import UserRole

pytestmark = pytest.mark.pg


def _to_domain(orm_user) -> DomainUser:
    return DomainUser(
        id=orm_user.id,
        email=orm_user.email or "",
        role=orm_user.role,
        email_verified=True,
        cognito_sub=orm_user.cognito_sub or "",
    )


@pytest.mark.asyncio
async def test_get_scan_for_user_owner_ok(
    pg_session, sample_user_factory, sample_project_factory, sample_scan_factory,
):
    owner_orm = await sample_user_factory(role=UserRole.MEMBER)
    project = await sample_project_factory(owner=owner_orm)
    scan = await sample_scan_factory(project=project)
    got = await scans_service.get_scan_for_user(
        pg_session, scan_id=scan.id, user=_to_domain(owner_orm),
    )
    assert got.id == scan.id


@pytest.mark.asyncio
async def test_get_scan_for_user_outsider_404(
    pg_session, sample_user_factory, sample_project_factory, sample_scan_factory,
):
    owner_orm = await sample_user_factory(role=UserRole.MEMBER)
    outsider_orm = await sample_user_factory(role=UserRole.MEMBER)
    project = await sample_project_factory(owner=owner_orm)
    scan = await sample_scan_factory(project=project)
    with pytest.raises(app_errors.ScanNotFound):
        await scans_service.get_scan_for_user(
            pg_session, scan_id=scan.id, user=_to_domain(outsider_orm),
        )


@pytest.mark.asyncio
async def test_get_scan_for_user_unknown_scan_404(pg_session, sample_user_factory):
    user_orm = await sample_user_factory(role=UserRole.MEMBER)
    with pytest.raises(app_errors.ScanNotFound):
        await scans_service.get_scan_for_user(
            pg_session, scan_id=999_999_999, user=_to_domain(user_orm),
        )


@pytest.mark.asyncio
async def test_require_editor_for_scan_reader_403(
    pg_session, sample_user_factory, sample_project_factory, sample_scan_factory,
):
    owner_orm = await sample_user_factory(role=UserRole.MEMBER)
    reader_orm = await sample_user_factory(role=UserRole.READER)
    project = await sample_project_factory(owner=owner_orm)
    scan = await sample_scan_factory(project=project)
    # READER global → viewer baseline; no ACL upgrade row → 403 on editor-required action.
    with pytest.raises(app_errors.Forbidden):
        await scans_service.require_editor_for_scan(
            pg_session, scan_id=scan.id, user=_to_domain(reader_orm),
        )
