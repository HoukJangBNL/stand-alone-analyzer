"""Pydantic schema sanity tests for W5-B upload models (B1 subset)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from flake_analysis.api.schemas.upload import (
    CreateScanRequest,
    MaterialItem,
    MaterialCreateRequest,
    MaterialCreateResponse,
)


def test_create_scan_request_accepts_minimal():
    req = CreateScanRequest(name="s1", material="graphene", image_count=10)
    assert req.extra_metadata == {}


def test_create_scan_request_rejects_zero_image_count():
    with pytest.raises(ValidationError):
        CreateScanRequest(name="s1", material="graphene", image_count=0)


def test_material_item_shape():
    from datetime import datetime, timezone
    m = MaterialItem(name="graphene", created_at=datetime.now(timezone.utc))
    assert m.name == "graphene"


def test_material_create_normalizes_name():
    """Name normalization is the route's job; schema only enforces non-empty."""
    req = MaterialCreateRequest(name="  Graphene  ")
    assert req.name == "  Graphene  "  # raw value preserved at schema layer


def test_material_create_response():
    r = MaterialCreateResponse(name="graphene", created=True)
    assert r.created is True


from flake_analysis.api.schemas.upload import (
    PresignRequest,
    PresignResponse,
    CompleteRequest,
    FinalizeResponse,
)


def test_presign_request_validates_sha256_hex():
    good = PresignRequest(
        filename="t.tif", sha256="a" * 64, grid_ix=0, grid_iy=0, size_bytes=1024,
    )
    assert good.sha256 == "a" * 64
    with pytest.raises(ValidationError):
        PresignRequest(filename="t.tif", sha256="zz", grid_ix=0, grid_iy=0, size_bytes=1024)


def test_presign_request_rejects_negative_grid():
    with pytest.raises(ValidationError):
        PresignRequest(
            filename="t.tif", sha256="a" * 64, grid_ix=-1, grid_iy=0, size_bytes=1024,
        )


def test_presign_response_round_trip():
    r = PresignResponse(
        put_url="https://s3.example/sig",
        headers={"x-amz-checksum-sha256": "QkFTRTY0=="},
        upload_item_id=42,
        s3_uri="s3://qpress-uploads/dev/scans/1/images/aa.tif",
    )
    assert r.upload_item_id == 42


def test_complete_request_basic():
    c = CompleteRequest(width=1024, height=768)
    assert c.width == 1024


def test_finalize_response():
    f = FinalizeResponse(status="ready", missing=0)
    assert f.status == "ready"
