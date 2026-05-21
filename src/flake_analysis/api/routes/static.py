"""Static asset routes per backend design §1.4 + mosaic-viewer §3.

All inputs flow through services.path_safety.safe_join — any traversal
attempt becomes a 400 ParamsInvalid before disk is touched.
"""
from __future__ import annotations
import json
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_manifest
from flake_analysis.api.errors import RawImageMissing, ThumbnailMissing
from flake_analysis.api.services.path_safety import safe_join
from flake_analysis.state.manifest import Manifest

router = APIRouter(prefix="/projects/{project_id}", tags=["static"])


def _read_thumb_metadata(folder: Path) -> tuple[str, list[str]]:
    """Return (params_hash, signature) for ETag construction."""
    manifest_p = folder / "manifest.json"
    params_hash = ""
    signature: list[str] = []
    if manifest_p.exists():
        m = json.loads(manifest_p.read_text(encoding="utf-8"))
        params_hash = m.get("steps", {}).get("thumbnails", {}).get("params_hash", "")
    idx_p = folder / "00_thumbnails" / "index.json"
    if idx_p.exists():
        idx = json.loads(idx_p.read_text(encoding="utf-8"))
        signature = list(idx.get("signature", []))
    return params_hash, signature


def _thumb_etag(folder: Path) -> str:
    ph, sig = _read_thumb_metadata(folder)
    return f"{ph}:{':'.join(sig[:2])}" if sig else ph


@router.get("/static/thumbnails/lod{lod}/{stem}.webp")
async def get_thumbnail(
    project_id: str,
    lod: int,
    stem: str,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    folder = Path(manifest.analysis_folder)
    cache = folder / "00_thumbnails"
    # safe_join validates EVERY part — the leading "lod{lod}" segment is
    # constructed server-side so we only need to validate `stem`.
    safe_stem = safe_join(cache / f"lod{lod}", f"{stem}.webp")
    if not safe_stem.exists():
        raise ThumbnailMissing(lod=lod, stem=stem)

    headers = {
        "Cache-Control": "public, max-age=86400, immutable",
        "ETag": _thumb_etag(folder),
    }
    return FileResponse(str(safe_stem), media_type="image/webp", headers=headers)
