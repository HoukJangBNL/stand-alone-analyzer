"""Data read endpoints per backend design §1.3."""
from __future__ import annotations
from fastapi import APIRouter, Depends
from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_manifest
from flake_analysis.api.schemas.data import ManifestModel
from flake_analysis.state.manifest import Manifest

router = APIRouter(prefix="/projects/{project_id}/data", tags=["data"])

@router.get("/manifest")
async def get_manifest_endpoint(
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
) -> ManifestModel:
    """Return manifest as JSON."""
    return ManifestModel.model_validate(manifest)
