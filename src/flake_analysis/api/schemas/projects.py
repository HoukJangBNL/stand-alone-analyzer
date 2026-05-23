"""Project lifecycle schemas (W10-C).

W10-C dropped the legacy `validate-paths` schemas and the
`analysis_folder/raw_images_dir/annotations_path` echo fields from
`ProjectHandle` — those served the pre-W10 path-only routing.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    """POST /projects body."""
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class PatchProjectRequest(BaseModel):
    """PATCH /projects/{pid} body — all fields optional, omitted = no change."""
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class ProjectHandle(BaseModel):
    """List item / GET response. Lightweight — does NOT carry scan_count."""
    project_id: str
    name: str
    owner_id: UUID
    description: str | None
    created_at: datetime


class ProjectListResponse(BaseModel):
    projects: list[ProjectHandle]


class ProjectDetail(ProjectHandle):
    """GET /projects/{pid} — adds scan_count."""
    scan_count: int
