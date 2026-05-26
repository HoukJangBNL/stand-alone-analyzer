"""W5-B2.4 — POST /scans/{id}/finalize and GET /scans/{id} tests."""
from __future__ import annotations

import boto3
import pytest
from httpx import ASGITransport, AsyncClient
from moto import mock_aws
from sqlalchemy import select

from flake_analysis.api.deps import get_db_session
from flake_analysis.api.main import app
from flake_analysis.db.models import UsageEvent

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


async def _full_upload(client, project_id, scan_image_count: int, n: int):
    """Helper: create a scan with image_count, complete `n` images."""
    sr = await client.post(
        f"/api/v1/projects/{project_id}/scans",
        json={"name": "s1", "material": "graphene", "image_count": scan_image_count},
    )
    scan_id = sr.json()["scan_id"]
    for i in range(n):
        sha = (f"{i:02x}" * 32)
        pr = await client.post(
            f"/api/v1/scans/{scan_id}/images/presign",
            json={"filename": f"t{i}.tif", "sha256": sha,
                  "grid_ix": i, "grid_iy": 0, "size_bytes": 100},
        )
        body = pr.json()
        key = body["s3_uri"].split("/", 3)[-1]
        boto3.client("s3", region_name="us-east-2").put_object(
            Bucket="qpress-uploads", Key=key, Body=b"x",
        )
        await client.post(
            f"/api/v1/scans/{scan_id}/images/{body['upload_item_id']}/complete",
            json={"width": 10, "height": 10},
        )
    return scan_id


@pytest.mark.asyncio
async def test_finalize_ready_when_count_matches(
    pg_session, sample_user_factory, sample_project_factory,
):
    user = await sample_user_factory()
    project = await sample_project_factory(owner=user)
    with mock_aws():
        _create_bucket()
        _override(pg_session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                scan_id = await _full_upload(c, project.id, scan_image_count=3, n=3)
                r = await c.post(f"/api/v1/scans/{scan_id}/finalize")
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["status"] == "ready"
                assert body["missing"] == 0

                usage = (await pg_session.execute(
                    select(UsageEvent).where(UsageEvent.kind == "scan_uploaded")
                )).scalars().all()
                assert len(usage) == 1
        finally:
            app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_finalize_409_when_incomplete(
    pg_session, sample_user_factory, sample_project_factory,
):
    user = await sample_user_factory()
    project = await sample_project_factory(owner=user)
    with mock_aws():
        _create_bucket()
        _override(pg_session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                scan_id = await _full_upload(c, project.id, scan_image_count=5, n=2)
                r = await c.post(f"/api/v1/scans/{scan_id}/finalize")
                assert r.status_code == 409
                body = r.json()
                assert body["detail"]["status"] == "incomplete"
                assert body["detail"]["missing"] == 3
        finally:
            app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_get_scan_detail(
    pg_session, sample_user_factory, sample_project_factory,
):
    user = await sample_user_factory()
    project = await sample_project_factory(owner=user)
    with mock_aws():
        _create_bucket()
        _override(pg_session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                scan_id = await _full_upload(c, project.id, scan_image_count=4, n=2)
                r = await c.get(f"/api/v1/scans/{scan_id}")
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["scan_id"] == scan_id
                assert body["uploaded_count"] == 2
                assert body["image_count"] == 4
                assert body["grid_ix_range"] == [0, 1]
                assert body["grid_iy_range"] == [0, 0]
                assert len(body["images"]) == 2
        finally:
            app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_get_scan_404(pg_session):
    with mock_aws():
        _create_bucket()
        _override(pg_session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                r = await c.get("/api/v1/scans/9999999")
                assert r.status_code == 404
        finally:
            app.dependency_overrides.pop(get_db_session, None)
