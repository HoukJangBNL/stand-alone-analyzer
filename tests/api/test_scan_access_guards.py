"""W11 — verify scan-level routes refuse callers outside the parent project."""
from __future__ import annotations

import boto3
import pytest
from httpx import ASGITransport, AsyncClient
from moto import mock_aws

from flake_analysis.api.auth import User as DomainUser, get_current_user
from flake_analysis.api.deps import get_db_session
from flake_analysis.api.main import app
from flake_analysis.db.models import UserRole

pytestmark = pytest.mark.pg


def _override_session(pg_session):
    async def _yield():
        yield pg_session
    app.dependency_overrides[get_db_session] = _yield


def _override_user(user: DomainUser):
    async def _yield():
        return user
    app.dependency_overrides[get_current_user] = _yield


def _to_domain(orm_user) -> DomainUser:
    return DomainUser(
        id=orm_user.id,
        email=orm_user.email or "",
        role=orm_user.role,
        email_verified=True,
        cognito_sub=orm_user.cognito_sub or "",
    )


def _create_bucket():
    boto3.client("s3", region_name="us-east-2").create_bucket(
        Bucket="qpress-uploads",
        CreateBucketConfiguration={"LocationConstraint": "us-east-2"},
    )


@pytest.mark.asyncio
async def test_presign_outsider_404(
    pg_session,
    sample_user_factory,
    sample_project_factory,
    sample_scan_factory,
):
    owner_orm = await sample_user_factory(role=UserRole.MEMBER)
    outsider_orm = await sample_user_factory(role=UserRole.MEMBER)
    project = await sample_project_factory(owner=owner_orm)
    scan = await sample_scan_factory(project=project)
    with mock_aws():
        _create_bucket()
        _override_session(pg_session)
        _override_user(_to_domain(outsider_orm))
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t",
            ) as c:
                r = await c.post(
                    f"/api/v1/scans/{scan.id}/images/presign",
                    json={
                        "filename": "tile_0_0.tif",
                        "sha256": "a" * 64,
                        "grid_ix": 0,
                        "grid_iy": 0,
                        "size_bytes": 1024,
                    },
                )
                assert r.status_code == 404, r.text
                assert r.json()["error"]["code"] == "scan_not_found"
        finally:
            app.dependency_overrides.pop(get_db_session, None)
            app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_complete_outsider_404(
    pg_session,
    sample_user_factory,
    sample_project_factory,
    sample_scan_factory,
):
    from flake_analysis.db.models.upload import (
        UploadItem,
        UploadItemStatus,
        UploadSession,
        UploadSessionStatus,
    )

    owner_orm = await sample_user_factory(role=UserRole.MEMBER)
    outsider_orm = await sample_user_factory(role=UserRole.MEMBER)
    project = await sample_project_factory(owner=owner_orm)
    scan = await sample_scan_factory(project=project)
    upl = UploadSession(
        scan_id=scan.id,
        status=UploadSessionStatus.ACTIVE,
        total_files=1,
        created_by_id=owner_orm.id,
    )
    pg_session.add(upl)
    await pg_session.flush()
    item = UploadItem(
        session_id=upl.id,
        filename="tile_0_0.tif",
        sha256="a" * 64,
        grid_ix=0,
        grid_iy=0,
        size_bytes=1024,
        s3_uri=f"s3://qpress-uploads/dev/scans/{scan.id}/tile_0_0.tif",
        status=UploadItemStatus.PENDING,
    )
    pg_session.add(item)
    await pg_session.flush()
    await pg_session.refresh(item)

    with mock_aws():
        _create_bucket()
        _override_session(pg_session)
        _override_user(_to_domain(outsider_orm))
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t",
            ) as c:
                r = await c.post(
                    f"/api/v1/scans/{scan.id}/images/{item.id}/complete",
                    json={"width": 1024, "height": 768},
                )
                assert r.status_code == 404, r.text
                assert r.json()["error"]["code"] == "scan_not_found"
        finally:
            app.dependency_overrides.pop(get_db_session, None)
            app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_finalize_outsider_404(
    pg_session, sample_user_factory, sample_project_factory, sample_scan_factory,
):
    owner_orm = await sample_user_factory(role=UserRole.MEMBER)
    outsider_orm = await sample_user_factory(role=UserRole.MEMBER)
    project = await sample_project_factory(owner=owner_orm)
    scan = await sample_scan_factory(project=project)
    with mock_aws():
        _create_bucket()
        _override_session(pg_session)
        _override_user(_to_domain(outsider_orm))
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://t",
            ) as c:
                r = await c.post(f"/api/v1/scans/{scan.id}/finalize")
                assert r.status_code == 404, r.text
                assert r.json()["error"]["code"] == "scan_not_found"
        finally:
            app.dependency_overrides.pop(get_db_session, None)
            app.dependency_overrides.pop(get_current_user, None)
