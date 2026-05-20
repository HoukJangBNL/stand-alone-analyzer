"""Project lifecycle endpoints per backend design §1.1."""
from __future__ import annotations
from fastapi import APIRouter, Depends
from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.schemas.projects import (
    CreateProjectRequest,
    ProjectHandle,
)
from flake_analysis.api.deps import _resolve_project_id

router = APIRouter(prefix="/projects", tags=["projects"])

@router.post("")
async def create_project(
    req: CreateProjectRequest,
    user: User = Depends(get_current_user),
) -> ProjectHandle:
    """Create project (v1: sets active project path)."""
    import flake_analysis.api.deps as deps_module
    deps_module._active_project = req.analysis_folder

    return ProjectHandle(
        project_id="local",
        analysis_folder=req.analysis_folder,
        raw_images_dir=req.raw_images_dir,
        annotations_path=req.annotations_path,
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
