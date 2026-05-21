"""Explorer routes per backend design §1.2/§1.3 + mosaic-viewer §3-§4."""
from __future__ import annotations
from fastapi import APIRouter, Depends, Query, Response

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_manifest
from flake_analysis.api.errors import ArtifactMissing, ExplorerStateMissing
from flake_analysis.api.schemas.explorer import (
    ExplorerFlakeDetail,
    ExplorerFlakeRow,
    ExplorerFlakesResponse,
    SaveExplorerStateParams,
    SaveExplorerStateResult,
    TileManifest,
)
from flake_analysis.api.services.explorer_service import (
    build_flake_detail,
    build_flake_table,
    build_tile_manifest,
)
from flake_analysis.state.manifest import Manifest

router = APIRouter(prefix="/projects/{project_id}", tags=["explorer"])


def _etag_for(manifest_obj: TileManifest) -> str:
    sig_part = ":".join(manifest_obj.signature[:2]) if manifest_obj.signature else ""
    return f"{manifest_obj.params_hash}:{sig_part}"


@router.get("/explorer/tile_manifest", response_model=TileManifest)
async def get_tile_manifest(
    project_id: str,
    response: Response,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Return the canonical TileManifest. Cache 24h, immutable per (params_hash, signature)."""
    try:
        tm = build_tile_manifest(manifest.analysis_folder)
    except FileNotFoundError as e:
        raise ArtifactMissing(missing=str(e))
    response.headers["ETag"] = _etag_for(tm)
    response.headers["Cache-Control"] = "public, max-age=86400, immutable"
    return tm
