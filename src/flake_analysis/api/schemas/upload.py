"""Pydantic schemas for the W5-B upload flow.

W5-B1 subset: materials + scan-create models. W5-B2 will append presign,
complete, finalize, and scan-detail schemas to this file.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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
