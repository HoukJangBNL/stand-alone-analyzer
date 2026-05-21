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


@router.get("/explorer/grid", response_model=TileManifest)
async def get_explorer_grid(
    project_id: str,
    response: Response,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Pinned decision #11: canonical alias of /tile_manifest per mosaic-viewer §4."""
    try:
        tm = build_tile_manifest(manifest.analysis_folder)
    except FileNotFoundError as e:
        raise ArtifactMissing(missing=str(e))
    response.headers["ETag"] = _etag_for(tm)
    response.headers["Cache-Control"] = "public, max-age=86400, immutable"
    return tm


@router.get("/explorer/flakes", response_model=ExplorerFlakesResponse)
async def get_explorer_flakes(
    project_id: str,
    include: str = Query("", description="Comma-separated cluster names"),
    exclude: str = Query("", description="Comma-separated cluster names"),
    size_min: int | None = Query(None, ge=1),
    size_max: int | None = Query(None, ge=1),
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Server-side filter per pinned decision #4."""
    inc = [s for s in include.split(",") if s] if include else []
    exc = [s for s in exclude.split(",") if s] if exclude else []
    try:
        df = build_flake_table(
            manifest.analysis_folder,
            include_labels=inc,
            exclude_labels=exc,
            size_min=size_min,
            size_max=size_max,
        )
    except FileNotFoundError as e:
        raise ArtifactMissing(missing=str(e))

    rows = [
        ExplorerFlakeRow(
            flake_id=int(r["flake_id"]),
            image_id=int(r["image_id"]),
            domains=int(r["domains"]),
            groups=str(r["groups"]),
            distance=str(r["distance"]),
            clipped=str(r["clipped"]),
            **{"pass": True},
        )
        for _, r in df.iterrows()
    ]
    return ExplorerFlakesResponse(rows=rows, total=len(rows))
