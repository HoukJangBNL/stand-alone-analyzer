"""Pydantic schemas for the W5-B upload flow.

W5-B1 subset: materials + scan-create models. W5-B2 appends presign,
complete, finalize, and scan-detail schemas.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


# ---- materials ----

class MaterialItem(BaseModel):
    name: str
    created_at: datetime


class MaterialListResponse(BaseModel):
    materials: list[MaterialItem]


class MaterialCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class MaterialCreateResponse(BaseModel):
    name: str
    created: bool


# ---- scans (create) ----

class CreateScanRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    material: str = Field(min_length=1, max_length=128)
    image_count: int = Field(gt=0, le=100_000)
    extra_metadata: dict[str, Any] = Field(default_factory=dict)


class ScanResponse(BaseModel):
    scan_id: int
    name: str
    material: str
    image_count: int
    extra_metadata: dict[str, Any]
    created_at: datetime


# ---- scans (detail) ----

class ImageSummary(BaseModel):
    image_id: int
    grid_ix: int
    grid_iy: int
    s3_uri: str
    sha256: str


class ScanDetailResponse(BaseModel):
    scan_id: int
    name: str
    material: str
    image_count: int
    extra_metadata: dict[str, Any]
    uploaded_count: int
    grid_ix_range: tuple[int, int] | None
    grid_iy_range: tuple[int, int] | None
    images: list[ImageSummary]


# ---- presign / complete / finalize ----

class PresignRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=512)
    sha256: str = Field(min_length=64, max_length=64)
    grid_ix: int = Field(ge=0)
    grid_iy: int = Field(ge=0)
    size_bytes: int = Field(gt=0, le=2_000_000_000)  # 2 GB hard cap

    @field_validator("sha256")
    @classmethod
    def _hex_lower(cls, v: str) -> str:
        if not _HEX64_RE.match(v):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return v


class PresignResponse(BaseModel):
    put_url: str
    headers: dict[str, str]
    upload_item_id: int
    s3_uri: str


class CompleteRequest(BaseModel):
    width: int = Field(gt=0, le=200_000)
    height: int = Field(gt=0, le=200_000)


class CompleteResponse(BaseModel):
    image_id: int


class FinalizeResponse(BaseModel):
    status: str  # "ready" or "incomplete"
    missing: int  # 0 when status=="ready"
