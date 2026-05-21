"""Project lifecycle endpoints per backend design §1.1."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Body, Depends
from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.schemas.projects import (
    CreateProjectRequest,
    ProjectHandle,
    ValidatePathsRequest,
    ValidatePathsResponse,
    PathStatus,
)
from flake_analysis.api.deps import (
    DEFAULT_ANALYSIS_FOLDER,
    DEFAULT_PROJECT_ID,
    _resolve_project_id,
)

router = APIRouter(prefix="/projects", tags=["projects"])

@router.post("")
async def create_project(
    req: Optional[CreateProjectRequest] = Body(default=None),
    user: User = Depends(get_current_user),
) -> ProjectHandle:
    """Create project (v1: sets active project path).

    Empty body → default project rooted at SAA_ANALYSIS_FOLDER.
    Body with analysis_folder → activates that path.
    Other paths (raw_images_dir, annotations_path) are echoed back verbatim
    for the frontend to persist alongside the manifest.
    """
    import os as _os
    import flake_analysis.api.deps as deps_module

    analysis_folder = (req.analysis_folder if req else None) or _os.environ.get(
        "SAA_ANALYSIS_FOLDER", DEFAULT_ANALYSIS_FOLDER
    )
    deps_module._active_project = analysis_folder

    return ProjectHandle(
        project_id=DEFAULT_PROJECT_ID,
        analysis_folder=analysis_folder,
        raw_images_dir=(req.raw_images_dir if req else None),
        annotations_path=(req.annotations_path if req else None),
    )

@router.get("/active")
async def get_active_project(
    user: User = Depends(get_current_user),
) -> ProjectHandle:
    """Get the active project (v1: single project)."""
    analysis_folder = _resolve_project_id("local")
    return ProjectHandle(
        project_id="local",
        analysis_folder=analysis_folder,
    )

@router.post("/validate-paths")
async def validate_paths(
    req: ValidatePathsRequest,
    user: User = Depends(get_current_user),
) -> ValidatePathsResponse:
    """Validate paths for existence, type, and permissions."""
    def check_path(path_str: str | None) -> PathStatus | None:
        if path_str is None:
            return None

        p = Path(path_str).resolve()
        exists = p.exists()
        is_dir = p.is_dir() if exists else False
        is_file = p.is_file() if exists else False
        readable = os.access(p, os.R_OK) if exists else False
        writable = os.access(p, os.W_OK) if exists else False

        return PathStatus(
            exists=exists,
            is_dir=is_dir,
            is_file=is_file,
            readable=readable,
            writable=writable,
            canonical=str(p),
        )

    return ValidatePathsResponse(
        analysis_folder=check_path(req.analysis_folder),
        raw_images_dir=check_path(req.raw_images_dir),
        annotations_path=check_path(req.annotations_path),
    )
