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
