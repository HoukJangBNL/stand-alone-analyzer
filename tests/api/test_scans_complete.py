"""W5-B2.3 — POST /scans/{sid}/images/{uid}/complete tests."""
from __future__ import annotations

import boto3
import pytest
from httpx import ASGITransport, AsyncClient
from moto import mock_aws
from sqlalchemy import select

from flake_analysis.api.deps import get_db_session
from flake_analysis.api.main import app
from flake_analysis.db.models.upload import (
    Image,
    UploadItem,
    UploadItemStatus,
)

pytestmark = pytest.mark.pg


def _override(pg_session):
    async def _yield():
        yield pg_session
    app.dependency_overrides[get_db_session] = _yield


def _create_bucket():
    boto3.client("s3", region_name="us-east-2").create_bucket(
        Bucket="qpress-uploads",
        CreateBucketConfiguration={"LocationConstraint": "us-east-2"},
    )


async def _scan_and_presign(client, project_id, sha="a" * 64, ix=0, iy=0):
    sr = await client.post(
        f"/api/v1/projects/{project_id}/scans",
        json={"name": "s1", "material": "graphene", "image_count": 2},
    )
    scan_id = sr.json()["scan_id"]
    pr = await client.post(
        f"/api/v1/scans/{scan_id}/images/presign",
        json={"filename": "t.tif", "sha256": sha,
              "grid_ix": ix, "grid_iy": iy, "size_bytes": 100},
    )
    return scan_id, pr.json()


def _put_object(key: str, body: bytes = b"x"):
    boto3.client("s3", region_name="us-east-2").put_object(
        Bucket="qpress-uploads", Key=key, Body=body,
    )


@pytest.mark.asyncio
async def test_complete_inserts_image_row(
    pg_session, sample_user_factory, sample_project_factory,
):
    user = await sample_user_factory()
    project = await sample_project_factory(owner=user)
    with mock_aws():
        _create_bucket()
        _override(pg_session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                scan_id, presign = await _scan_and_presign(c, project.id)
                # Simulate a successful S3 PUT
                key = presign["s3_uri"].split("/", 3)[-1]
                _put_object(key)

                r = await c.post(
                    f"/api/v1/scans/{scan_id}/images/{presign['upload_item_id']}/complete",
                    json={"width": 1024, "height": 768},
                )
                assert r.status_code == 200, r.text
                image_id = r.json()["image_id"]

                img = (await pg_session.execute(
                    select(Image).where(Image.id == image_id)
                )).scalar_one()
                assert img.scan_id == scan_id
                assert img.width == 1024 and img.height == 768
                assert img.sha256 == "a" * 64

                item = (await pg_session.execute(
                    select(UploadItem).where(UploadItem.id == presign["upload_item_id"])
                )).scalar_one()
                assert item.status == UploadItemStatus.UPLOADED
                assert item.image_id == image_id
        finally:
            app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_complete_409_when_s3_object_missing(
    pg_session, sample_user_factory, sample_project_factory,
):
    user = await sample_user_factory()
    project = await sample_project_factory(owner=user)
    with mock_aws():
        _create_bucket()
        _override(pg_session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                scan_id, presign = await _scan_and_presign(c, project.id)
                # Do NOT put the object — head_object should 404 → API 409
                r = await c.post(
                    f"/api/v1/scans/{scan_id}/images/{presign['upload_item_id']}/complete",
                    json={"width": 10, "height": 10},
                )
                assert r.status_code == 409
                assert "s3" in r.json()["detail"].lower()
        finally:
            app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_complete_404_when_upload_item_missing(
    pg_session, sample_user_factory, sample_project_factory,
):
    user = await sample_user_factory()
    project = await sample_project_factory(owner=user)
    with mock_aws():
        _create_bucket()
        _override(pg_session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                sr = await c.post(
                    f"/api/v1/projects/{project.id}/scans",
                    json={"name": "s1", "material": "graphene", "image_count": 1},
                )
                scan_id = sr.json()["scan_id"]
                r = await c.post(
                    f"/api/v1/scans/{scan_id}/images/9999999/complete",
                    json={"width": 10, "height": 10},
                )
                assert r.status_code == 404
        finally:
            app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_complete_is_idempotent_on_already_uploaded(
    pg_session, sample_user_factory, sample_project_factory,
):
    user = await sample_user_factory()
    project = await sample_project_factory(owner=user)
    with mock_aws():
        _create_bucket()
        _override(pg_session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                scan_id, presign = await _scan_and_presign(c, project.id)
                key = presign["s3_uri"].split("/", 3)[-1]
                _put_object(key)
                r1 = await c.post(
                    f"/api/v1/scans/{scan_id}/images/{presign['upload_item_id']}/complete",
                    json={"width": 10, "height": 10},
                )
                assert r1.status_code == 200
                r2 = await c.post(
                    f"/api/v1/scans/{scan_id}/images/{presign['upload_item_id']}/complete",
                    json={"width": 10, "height": 10},
                )
                assert r2.status_code == 200
                assert r2.json()["image_id"] == r1.json()["image_id"]
        finally:
            app.dependency_overrides.pop(get_db_session, None)
