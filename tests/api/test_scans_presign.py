"""W5-B2.2 — POST /scans/{scan_id}/images/presign tests."""
from __future__ import annotations

import logging

import boto3
import pytest
from httpx import ASGITransport, AsyncClient
from moto import mock_aws
from sqlalchemy import select

from flake_analysis.api.deps import get_db_session
from flake_analysis.api.main import app
from flake_analysis.db.models.upload import (
    UploadItem,
    UploadItemStatus,
    UploadSession,
    UploadSessionStatus,
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


async def _create_scan(client, project_id, image_count=4):
    r = await client.post(
        f"/api/v1/projects/{project_id}/scans",
        json={"name": "s1", "material": "graphene", "image_count": image_count},
    )
    assert r.status_code == 201, r.text
    return r.json()["scan_id"]


@pytest.mark.asyncio
async def test_presign_creates_session_and_item(
    pg_session, sample_user_factory, sample_project_factory,
):
    user = await sample_user_factory()
    project = await sample_project_factory(owner=user)
    with mock_aws():
        _create_bucket()
        _override(pg_session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                scan_id = await _create_scan(c, project.id)
                r = await c.post(
                    f"/api/v1/scans/{scan_id}/images/presign",
                    json={
                        "filename": "tile_0_0.tif",
                        "sha256": "a" * 64,
                        "grid_ix": 0,
                        "grid_iy": 0,
                        "size_bytes": 10485760,
                    },
                )
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["put_url"].startswith("https://")
                assert "x-amz-checksum-sha256" in body["headers"]
                assert isinstance(body["upload_item_id"], int)
                assert body["s3_uri"].startswith("s3://qpress-uploads/dev/scans/")
                # DB side: 1 active session, 1 pending item
                sess = (await pg_session.execute(
                    select(UploadSession).where(UploadSession.scan_id == scan_id)
                )).scalar_one()
                assert sess.status == UploadSessionStatus.ACTIVE
                assert sess.total_files == 4
                item = (await pg_session.execute(
                    select(UploadItem).where(UploadItem.id == body["upload_item_id"])
                )).scalar_one()
                assert item.status == UploadItemStatus.PENDING
                assert item.sha256 == "a" * 64
                assert item.grid_ix == 0
        finally:
            app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_presign_rejects_duplicate_sha256(
    pg_session, sample_user_factory, sample_project_factory,
):
    user = await sample_user_factory()
    project = await sample_project_factory(owner=user)
    with mock_aws():
        _create_bucket()
        _override(pg_session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                scan_id = await _create_scan(c, project.id)
                body = {
                    "filename": "a.tif", "sha256": "b" * 64,
                    "grid_ix": 0, "grid_iy": 0, "size_bytes": 100,
                }
                ok = await c.post(f"/api/v1/scans/{scan_id}/images/presign", json=body)
                assert ok.status_code == 200
                dup = await c.post(
                    f"/api/v1/scans/{scan_id}/images/presign",
                    json={**body, "grid_ix": 1},  # different grid, same sha
                )
                assert dup.status_code == 409
                assert "sha256" in dup.json()["detail"].lower()
        finally:
            app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_presign_rejects_duplicate_grid(
    pg_session, sample_user_factory, sample_project_factory,
):
    user = await sample_user_factory()
    project = await sample_project_factory(owner=user)
    with mock_aws():
        _create_bucket()
        _override(pg_session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                scan_id = await _create_scan(c, project.id)
                ok = await c.post(
                    f"/api/v1/scans/{scan_id}/images/presign",
                    json={"filename": "a.tif", "sha256": "c" * 64,
                          "grid_ix": 2, "grid_iy": 3, "size_bytes": 100},
                )
                assert ok.status_code == 200
                dup = await c.post(
                    f"/api/v1/scans/{scan_id}/images/presign",
                    json={"filename": "b.tif", "sha256": "d" * 64,
                          "grid_ix": 2, "grid_iy": 3, "size_bytes": 100},
                )
                assert dup.status_code == 409
                assert "grid" in dup.json()["detail"].lower()
        finally:
            app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_presign_409_sha256_collision_logs_event(
    caplog, pg_session, sample_user_factory, sample_project_factory,
):
    """A4: 409 sha256 collision in presign emits structured INFO log."""
    user = await sample_user_factory()
    project = await sample_project_factory(owner=user)
    with mock_aws():
        _create_bucket()
        _override(pg_session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                scan_id = await _create_scan(c, project.id)
                body = {
                    "filename": "a.tif", "sha256": "f" * 64,
                    "grid_ix": 0, "grid_iy": 0, "size_bytes": 100,
                }
                ok = await c.post(f"/api/v1/scans/{scan_id}/images/presign", json=body)
                assert ok.status_code == 200

                caplog.clear()
                with caplog.at_level(logging.INFO, logger="flake_analysis.api.routes.scans"):
                    dup = await c.post(
                        f"/api/v1/scans/{scan_id}/images/presign",
                        json={**body, "grid_ix": 1},  # different grid, same sha
                    )
                assert dup.status_code == 409

                matches = [
                    r for r in caplog.records
                    if getattr(r, "event", None) == "presign_collision_sha256"
                ]
                assert matches, (
                    f"expected a record with extra={{'event': 'presign_collision_sha256'}}, "
                    f"got events={[getattr(r, 'event', None) for r in caplog.records]}"
                )
                rec = matches[0]
                assert rec.levelno == logging.INFO
                assert getattr(rec, "scan_id", None) == scan_id
        finally:
            app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_presign_404_when_scan_missing(pg_session):
    with mock_aws():
        _create_bucket()
        _override(pg_session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                r = await c.post(
                    "/api/v1/scans/9999999/images/presign",
                    json={"filename": "a.tif", "sha256": "e" * 64,
                          "grid_ix": 0, "grid_iy": 0, "size_bytes": 1},
                )
                assert r.status_code == 404
        finally:
            app.dependency_overrides.pop(get_db_session, None)
