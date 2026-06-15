"""Tests for SAM results API routes (Task 5: S3-backed results read).

Tests two routes:
1. GET /projects/{pid}/scans/{sid}/sam/results - reads per_image_results.json from S3
2. GET /projects/{pid}/scans/{sid}/sam/masks - returns presigned GET URLs for masks
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.errors import AppError, app_error_handler
from flake_analysis.api.logging_ctx import RequestIdMiddleware
from flake_analysis.api.routes import run as run_route


def _make_app() -> FastAPI:
    """Mini-app exposing only the run router."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(run_route.router, prefix="/api/v1")
    return app


@pytest.fixture
def mock_s3_client():
    """Mock boto3.client globally for get_object and list_objects_v2."""
    with patch("boto3.client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client_factory.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_scan_access():
    """Mock scans_service.get_scan_for_user to bypass DB access checks."""
    with patch("flake_analysis.api.routes.run.scans_service") as mock_svc:
        # Return a fake scan object
        fake_scan = MagicMock()
        fake_scan.id = 42
        fake_scan.project_id = "test-project"
        mock_svc.get_scan_for_user = AsyncMock(return_value=fake_scan)
        yield mock_svc


@pytest.mark.asyncio
async def test_get_sam_results_returns_json_summary(
    mock_s3_client,
    mock_scan_access,
    monkeypatch,
):
    """GET /sam/results returns the parsed per_image_results.json from S3."""
    monkeypatch.setenv("SAA_S3_BUCKET", "test-bucket")

    scan_id = 42
    project_id = "test-project"

    # Mock S3 get_object to return a valid per_image_results.json
    mock_results = {
        "images": 10,
        "masks_total": 42,
        "errors": 0,
        "per_image": [{"image": "ix001_iy001.png", "masks": 5}],
    }
    mock_body = MagicMock()
    mock_body.read.return_value = json.dumps(mock_results).encode("utf-8")
    mock_s3_client.get_object.return_value = {"Body": mock_body}

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/projects/{project_id}/scans/{scan_id}/run/sam/results"
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["images"] == 10
    assert data["masks_total"] == 42
    assert len(data["per_image"]) == 1

    # Verify S3 call used correct key
    mock_s3_client.get_object.assert_called_once()
    call_kwargs = mock_s3_client.get_object.call_args.kwargs
    assert call_kwargs["Bucket"] == "test-bucket"
    assert call_kwargs["Key"] == f"scans/{scan_id}/07_sam/per_image_results.json"


@pytest.mark.asyncio
async def test_get_sam_results_404_when_missing(
    mock_s3_client,
    mock_scan_access,
    monkeypatch,
):
    """GET /sam/results returns 404 when per_image_results.json doesn't exist."""
    monkeypatch.setenv("SAA_S3_BUCKET", "test-bucket")

    scan_id = 42
    project_id = "test-project"

    # Mock S3 to raise NoSuchKey
    error_response = {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}
    mock_s3_client.get_object.side_effect = ClientError(error_response, "GetObject")

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/projects/{project_id}/scans/{scan_id}/run/sam/results"
        )

    assert resp.status_code == 404
    assert "no sam results" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_get_sam_masks_returns_presigned_urls(
    mock_s3_client,
    mock_scan_access,
    monkeypatch,
):
    """GET /sam/masks returns presigned GET URLs for all masks under 07_sam/."""
    monkeypatch.setenv("SAA_S3_BUCKET", "test-bucket")

    scan_id = 42
    project_id = "test-project"

    # Mock S3 list_objects_v2 to return mask keys
    mock_s3_client.list_objects_v2.return_value = {
        "Contents": [
            {"Key": f"scans/{scan_id}/07_sam/masks/img1/mask_0.png"},
            {"Key": f"scans/{scan_id}/07_sam/masks/img1/mask_1.png"},
            {"Key": f"scans/{scan_id}/07_sam/masks/img2/mask_0.png"},
        ]
    }

    # Mock generate_presigned_url
    def fake_presign(ClientMethod, Params, ExpiresIn, HttpMethod):
        key = Params["Key"]
        return f"https://s3.example.com/{key}?presigned=true"

    mock_s3_client.generate_presigned_url.side_effect = fake_presign

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/projects/{project_id}/scans/{scan_id}/run/sam/masks"
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "masks" in data
    assert len(data["masks"]) == 3
    # Verify presigned URLs were generated
    for mask_item in data["masks"]:
        assert "key" in mask_item
        assert "url" in mask_item
        assert "presigned=true" in mask_item["url"]

    # Verify list call used correct prefix
    mock_s3_client.list_objects_v2.assert_called_once()
    call_kwargs = mock_s3_client.list_objects_v2.call_args.kwargs
    assert call_kwargs["Bucket"] == "test-bucket"
    assert call_kwargs["Prefix"] == f"scans/{scan_id}/07_sam/"


@pytest.mark.asyncio
async def test_get_sam_masks_empty_when_no_run(
    mock_s3_client,
    mock_scan_access,
    monkeypatch,
):
    """GET /sam/masks returns empty list when no SAM run has occurred."""
    monkeypatch.setenv("SAA_S3_BUCKET", "test-bucket")

    scan_id = 42
    project_id = "test-project"

    # Mock S3 to return no objects
    mock_s3_client.list_objects_v2.return_value = {}

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/projects/{project_id}/scans/{scan_id}/run/sam/masks"
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["masks"] == []
