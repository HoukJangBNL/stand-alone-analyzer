"""B6 — upload-path errors must use ErrorEnvelope shape (with request_id).

Frontend ApiError parser only extracts `request_id` from envelope-shaped
bodies. Bare HTTPException paths drop it. These tests pin the envelope
contract on previously-raw raise sites in routes/scans.py.
"""
from __future__ import annotations

import boto3
import pytest
from httpx import ASGITransport, AsyncClient
from moto import mock_aws

from flake_analysis.api.deps import get_db_session
from flake_analysis.api.main import app

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


def _assert_envelope(body: dict, expected_code: str) -> None:
    """Envelope shape contract: body has error.{code,message,details,request_id}."""
    assert "error" in body, f"missing 'error' key: {body}"
    err = body["error"]
    assert err.get("code") == expected_code, f"code mismatch: {err}"
    assert isinstance(err.get("message"), str) and err["message"], err
    assert "details" in err and isinstance(err["details"], dict)
    assert "request_id" in err
    assert err["request_id"], "request_id must be non-empty"


async def _create_scan(client, project_id, image_count=4):
    r = await client.post(
        f"/api/v1/projects/{project_id}/scans",
        json={"name": "s1", "material": "graphene", "image_count": image_count},
    )
    assert r.status_code == 201, r.text
    return r.json()["scan_id"]


@pytest.mark.asyncio
async def test_presign_404_scan_not_found_returns_envelope(
    pg_session, sample_user_factory, sample_project_factory,
):
    """Presign on a nonexistent scan: 404 with envelope (was raw HTTPException)."""
    user = await sample_user_factory()
    await sample_project_factory(owner=user)
    with mock_aws():
        _create_bucket()
        _override(pg_session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                r = await c.post(
                    "/api/v1/scans/99999999/images/presign",
                    json={
                        "filename": "tile_0_0.tif",
                        "sha256": "a" * 64,
                        "grid_ix": 0,
                        "grid_iy": 0,
                        "size_bytes": 100,
                    },
                )
                assert r.status_code == 404, r.text
                _assert_envelope(r.json(), "scan_not_found")
        finally:
            app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_finalize_404_scan_not_found_returns_envelope(
    pg_session, sample_user_factory, sample_project_factory,
):
    """Finalize on a nonexistent scan: 404 with envelope."""
    user = await sample_user_factory()
    await sample_project_factory(owner=user)
    with mock_aws():
        _create_bucket()
        _override(pg_session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                r = await c.post("/api/v1/scans/99999999/finalize")
                assert r.status_code == 404, r.text
                _assert_envelope(r.json(), "scan_not_found")
        finally:
            app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_get_scan_404_returns_envelope(
    pg_session, sample_user_factory, sample_project_factory,
):
    """GET /scans/{id} on nonexistent scan: 404 with envelope."""
    user = await sample_user_factory()
    await sample_project_factory(owner=user)
    with mock_aws():
        _create_bucket()
        _override(pg_session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                r = await c.get("/api/v1/scans/99999999")
                assert r.status_code == 404, r.text
                _assert_envelope(r.json(), "scan_not_found")
        finally:
            app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_complete_404_upload_item_not_found_returns_envelope(
    pg_session, sample_user_factory, sample_project_factory,
):
    """Complete on a nonexistent upload_item: 404 with envelope."""
    user = await sample_user_factory()
    project = await sample_project_factory(owner=user)
    with mock_aws():
        _create_bucket()
        _override(pg_session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                scan_id = await _create_scan(c, project.id)
                r = await c.post(
                    f"/api/v1/scans/{scan_id}/images/99999999/complete",
                    json={"width": 1024, "height": 1024},
                )
                assert r.status_code == 404, r.text
                _assert_envelope(r.json(), "upload_item_not_found")
        finally:
            app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_presign_409_sha256_collision_returns_envelope(
    pg_session, sample_user_factory, sample_project_factory,
):
    """Presign sha256 dup against finalized image: 409 with envelope."""
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
                    json={**body, "grid_ix": 1},
                )
                assert dup.status_code == 409, dup.text
                _assert_envelope(dup.json(), "presign_collision_sha256")
        finally:
            app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.asyncio
async def test_finalize_409_incomplete_returns_envelope_with_details(
    pg_session, sample_user_factory, sample_project_factory,
):
    """Finalize when uploads incomplete: 409 envelope, details has missing/uploaded/expected."""
    user = await sample_user_factory()
    project = await sample_project_factory(owner=user)
    with mock_aws():
        _create_bucket()
        _override(pg_session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                scan_id = await _create_scan(c, project.id, image_count=5)
                r = await c.post(f"/api/v1/scans/{scan_id}/finalize")
                assert r.status_code == 409, r.text
                body = r.json()
                _assert_envelope(body, "finalize_incomplete")
                d = body["error"]["details"]
                assert d["missing"] == 5
                assert d["uploaded"] == 0
                assert d["expected"] == 5
        finally:
            app.dependency_overrides.pop(get_db_session, None)
