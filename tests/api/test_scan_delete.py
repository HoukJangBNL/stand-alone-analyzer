"""Tests for DELETE /scans/{scan_id} (W12)."""
from __future__ import annotations

import boto3
import pytest
from httpx import ASGITransport, AsyncClient
from moto import mock_aws

from flake_analysis.api.auth import User as DomainUser, UserRole, get_current_user
from flake_analysis.api.deps import get_db_session
from flake_analysis.api.main import app
from flake_analysis.db.models import ProjectRole, ProjectUser
from flake_analysis.db.models.user import User as ORMUser


def _to_domain(orm_user: ORMUser) -> DomainUser:
    return DomainUser(
        id=orm_user.id,
        email=orm_user.email,
        role=orm_user.role,
        email_verified=True,
        cognito_sub=orm_user.cognito_sub or "test-sub",
    )


def _override_session(pg_session):
    async def _override():
        yield pg_session
    return _override


def _override_user(domain_user: DomainUser):
    async def _override():
        return domain_user
    return _override


def _create_bucket():
    boto3.client("s3", region_name="us-east-2").create_bucket(
        Bucket="qpress-uploads",
        CreateBucketConfiguration={"LocationConstraint": "us-east-2"},
    )


@pytest.mark.asyncio
@pytest.mark.pg
async def test_delete_outsider_404(
    pg_session, sample_user_factory, sample_project_factory, sample_scan_factory,
    monkeypatch,
):
    monkeypatch.setenv("SAA_S3_BUCKET", "qpress-uploads")
    owner = await sample_user_factory(role=UserRole.MEMBER)
    outsider = await sample_user_factory(role=UserRole.MEMBER)
    project = await sample_project_factory(owner=owner)
    scan = await sample_scan_factory(project=project)

    app.dependency_overrides[get_db_session] = _override_session(pg_session)
    app.dependency_overrides[get_current_user] = _override_user(_to_domain(outsider))
    try:
        with mock_aws():
            _create_bucket()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete(f"/api/v1/scans/{scan.id}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "scan_not_found"
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
@pytest.mark.pg
async def test_delete_viewer_403(
    pg_session, sample_user_factory, sample_project_factory, sample_scan_factory,
    monkeypatch,
):
    monkeypatch.setenv("SAA_S3_BUCKET", "qpress-uploads")
    owner = await sample_user_factory(role=UserRole.MEMBER)
    viewer = await sample_user_factory(role=UserRole.MEMBER)
    project = await sample_project_factory(owner=owner)
    scan = await sample_scan_factory(project=project)
    pg_session.add(ProjectUser(
        project_id=project.id, user_id=viewer.id, project_role=ProjectRole.VIEWER,
    ))
    await pg_session.commit()

    app.dependency_overrides[get_db_session] = _override_session(pg_session)
    app.dependency_overrides[get_current_user] = _override_user(_to_domain(viewer))
    try:
        with mock_aws():
            _create_bucket()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete(f"/api/v1/scans/{scan.id}")
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "forbidden"
        assert body["error"]["details"]["action"] == "scan_edit"
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
@pytest.mark.pg
async def test_delete_owner_succeeds_and_wipes_s3(
    pg_session, sample_user_factory, sample_project_factory, sample_scan_factory,
    monkeypatch,
):
    monkeypatch.setenv("SAA_S3_BUCKET", "qpress-uploads")
    owner = await sample_user_factory(role=UserRole.MEMBER)
    project = await sample_project_factory(owner=owner)
    scan = await sample_scan_factory(project=project)

    app.dependency_overrides[get_db_session] = _override_session(pg_session)
    app.dependency_overrides[get_current_user] = _override_user(_to_domain(owner))
    try:
        with mock_aws():
            _create_bucket()
            s3 = boto3.client("s3", region_name="us-east-2")
            s3.put_object(
                Bucket="qpress-uploads",
                Key=f"dev/scans/{scan.id}/images/a.png",
                Body=b"x",
            )
            s3.put_object(
                Bucket="qpress-uploads",
                Key=f"dev/scans/{scan.id}/images/b.png",
                Body=b"y",
            )

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.delete(f"/api/v1/scans/{scan.id}")

            assert resp.status_code == 204

            remaining = s3.list_objects_v2(
                Bucket="qpress-uploads", Prefix=f"dev/scans/{scan.id}/"
            ).get("Contents")
            assert remaining is None or len(remaining) == 0

        # DB row gone
        from sqlalchemy import select
        from flake_analysis.db.models import Scan
        gone = (await pg_session.execute(
            select(Scan).where(Scan.id == scan.id)
        )).scalar_one_or_none()
        assert gone is None
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
@pytest.mark.pg
async def test_delete_idempotent_second_call_404(
    pg_session, sample_user_factory, sample_project_factory, sample_scan_factory,
    monkeypatch,
):
    monkeypatch.setenv("SAA_S3_BUCKET", "qpress-uploads")
    owner = await sample_user_factory(role=UserRole.MEMBER)
    project = await sample_project_factory(owner=owner)
    scan = await sample_scan_factory(project=project)

    app.dependency_overrides[get_db_session] = _override_session(pg_session)
    app.dependency_overrides[get_current_user] = _override_user(_to_domain(owner))
    try:
        with mock_aws():
            _create_bucket()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                first = await client.delete(f"/api/v1/scans/{scan.id}")
                assert first.status_code == 204
                second = await client.delete(f"/api/v1/scans/{scan.id}")
                assert second.status_code == 404
                assert second.json()["error"]["code"] == "scan_not_found"
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_current_user, None)
