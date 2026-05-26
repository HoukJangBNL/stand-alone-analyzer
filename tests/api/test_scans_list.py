"""W10-C: GET /projects/{pid}/scans listing."""
from __future__ import annotations

import boto3
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from moto import mock_aws

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.routes import scans as scans_route

pytestmark = pytest.mark.pg


def _make_app() -> FastAPI:
    """Build a minimal FastAPI app exposing only the scans router.

    Mirrors `tests/api/test_projects.py::_make_app` — sidesteps W10-C Task 4's
    not-yet-migrated analysis routers (data/run/selector/clustering/explorer/
    static) which still use the pre-W10-B `Depends(get_active_analysis)` shape
    and crash at route-collection time. Once Task 4 lands, both files can
    switch to importing the full `app` from `flake_analysis.api.main`.
    """
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(scans_route.router, prefix="/api/v1")
    return app


async def _client(pg_session, current_user):
    """Wire the test app's get_db_session + get_current_user to test fixtures."""
    from flake_analysis.api.auth import get_current_user
    from flake_analysis.api.deps import get_db_session

    app = _make_app()

    async def _override_db():
        yield pg_session

    app.dependency_overrides[get_db_session] = _override_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _create_bucket():
    boto3.client("s3", region_name="us-east-2").create_bucket(
        Bucket="qpress-uploads",
        CreateBucketConfiguration={"LocationConstraint": "us-east-2"},
    )


async def _upload_n_images(client, scan_id: int, n: int) -> None:
    """Drive presign+S3 PUT+complete for `n` images on the given scan."""
    s3 = boto3.client("s3", region_name="us-east-2")
    for i in range(n):
        sha = f"{i:02x}" * 32
        pr = await client.post(
            f"/api/v1/scans/{scan_id}/images/presign",
            json={
                "filename": f"t{i}.tif",
                "sha256": sha,
                "grid_ix": i,
                "grid_iy": 0,
                "size_bytes": 100,
            },
        )
        assert pr.status_code == 200, pr.text
        body = pr.json()
        key = body["s3_uri"].split("/", 3)[-1]
        s3.put_object(Bucket="qpress-uploads", Key=key, Body=b"x")
        cr = await client.post(
            f"/api/v1/scans/{scan_id}/images/{body['upload_item_id']}/complete",
            json={"width": 10, "height": 10},
        )
        assert cr.status_code == 200, cr.text


@pytest.mark.asyncio
async def test_list_scans_for_project_empty(
    pg_session, sample_user_factory, sample_project_factory,
):
    user = await sample_user_factory()
    proj = await sample_project_factory(owner=user)
    await pg_session.commit()

    async with await _client(pg_session, user) as client:
        r = await client.get(f"/api/v1/projects/{proj.id}/scans")
        assert r.status_code == 200, r.text
        assert r.json() == {"scans": []}


@pytest.mark.asyncio
async def test_list_scans_returns_only_for_that_project(
    pg_session, sample_user_factory, sample_project_factory, sample_scan_factory,
):
    user = await sample_user_factory()
    p1 = await sample_project_factory(owner=user)
    p2 = await sample_project_factory(owner=user)
    await sample_scan_factory(project=p1, name="s1")
    await sample_scan_factory(project=p1, name="s2")
    await sample_scan_factory(project=p2, name="s3")
    await pg_session.commit()

    async with await _client(pg_session, user) as client:
        r = await client.get(f"/api/v1/projects/{p1.id}/scans")
        assert r.status_code == 200, r.text
        names = [s["name"] for s in r.json()["scans"]]
        assert sorted(names) == ["s1", "s2"]


# --- D1: uploaded_count + status fields ---------------------------------


@pytest.mark.asyncio
async def test_list_scans_zero_uploaded_is_draft(
    pg_session, sample_user_factory, sample_project_factory,
):
    """A freshly-created scan with image_count=10 and no uploads:
    list returns uploaded_count=0, status='draft'."""
    user = await sample_user_factory()
    proj = await sample_project_factory(owner=user)
    await pg_session.commit()

    async with await _client(pg_session, user) as client:
        cr = await client.post(
            f"/api/v1/projects/{proj.id}/scans",
            json={"name": "s-zero", "material": "graphene", "image_count": 10},
        )
        assert cr.status_code == 201, cr.text

        r = await client.get(f"/api/v1/projects/{proj.id}/scans")
        assert r.status_code == 200, r.text
        scans = r.json()["scans"]
        assert len(scans) == 1
        s = scans[0]
        assert s["image_count"] == 10
        assert s["uploaded_count"] == 0
        assert s["status"] == "draft"


@pytest.mark.asyncio
async def test_list_scans_partial_uploaded_is_draft(
    pg_session, sample_user_factory, sample_project_factory,
):
    """Scan with image_count=10 and 3 completed images:
    list returns uploaded_count=3, status='draft'."""
    user = await sample_user_factory()
    proj = await sample_project_factory(owner=user)
    await pg_session.commit()

    with mock_aws():
        _create_bucket()
        async with await _client(pg_session, user) as client:
            cr = await client.post(
                f"/api/v1/projects/{proj.id}/scans",
                json={"name": "s-partial", "material": "graphene", "image_count": 10},
            )
            scan_id = cr.json()["scan_id"]
            await _upload_n_images(client, scan_id, 3)

            r = await client.get(f"/api/v1/projects/{proj.id}/scans")
            assert r.status_code == 200, r.text
            scans = r.json()["scans"]
            assert len(scans) == 1
            s = scans[0]
            assert s["image_count"] == 10
            assert s["uploaded_count"] == 3
            assert s["status"] == "draft"


@pytest.mark.asyncio
async def test_list_scans_all_uploaded_and_finalized_is_ready(
    pg_session, sample_user_factory, sample_project_factory,
):
    """Scan with image_count=10, 10 completed images, finalized:
    list returns uploaded_count=10, status='ready'."""
    user = await sample_user_factory()
    proj = await sample_project_factory(owner=user)
    await pg_session.commit()

    with mock_aws():
        _create_bucket()
        async with await _client(pg_session, user) as client:
            cr = await client.post(
                f"/api/v1/projects/{proj.id}/scans",
                json={"name": "s-ready", "material": "graphene", "image_count": 10},
            )
            scan_id = cr.json()["scan_id"]
            await _upload_n_images(client, scan_id, 10)

            fr = await client.post(f"/api/v1/scans/{scan_id}/finalize")
            assert fr.status_code == 200, fr.text
            assert fr.json()["status"] == "ready"

            r = await client.get(f"/api/v1/projects/{proj.id}/scans")
            assert r.status_code == 200, r.text
            scans = r.json()["scans"]
            assert len(scans) == 1
            s = scans[0]
            assert s["image_count"] == 10
            assert s["uploaded_count"] == 10
            assert s["status"] == "ready"
