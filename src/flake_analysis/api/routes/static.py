"""Static asset routes per backend design §1.4 + mosaic-viewer §3.

All inputs flow through services.path_safety.safe_join — any traversal
attempt becomes a 400 ParamsInvalid before disk is touched.
"""
from __future__ import annotations
import json
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, Response
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


def _read_thumbnail_cache_dir(folder: Path) -> Path | None:
    """Return cache_dir from 00_thumbnails/index.json, or None for legacy layout."""
    idx_p = folder / "00_thumbnails" / "index.json"
    if not idx_p.exists():
        return None
    idx = json.loads(idx_p.read_text(encoding="utf-8"))
    cache_dir = idx.get("cache_dir")
    if not cache_dir:
        return None
    return Path(cache_dir)


@router.get("/static/thumbnails/lod{lod}/{stem}.webp")
async def get_thumbnail(
    project_id: str,
    lod: int,
    stem: str,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Serve a thumbnail tile.

    Plan 5 Task 7 (deployment-design §2.2 Option B): when the project's
    00_thumbnails/index.json declares a `cache_dir`, return a 200 with
    an `X-Accel-Redirect` header pointing at /_tiles_internal/<sha>/...
    so nginx (alias /var/cache/stand-alone-analyzer/thumbnails/) ships
    the bytes off the asyncio loop. Legacy projects (no `cache_dir` in
    index.json — v0.2.15 layout) keep the Plan 4 `FileResponse`
    fallback so existing analysis folders still load.
    """
    folder = Path(manifest.analysis_folder)
    headers = {
        "Cache-Control": "public, max-age=86400, immutable",
        "ETag": _thumb_etag(folder),
    }

    cache_dir = _read_thumbnail_cache_dir(folder)
    if cache_dir is not None:
        # cache_dir = .../<sha>/   — the basename is what nginx aliases under.
        sha = cache_dir.name
        # Validate <stem> with the same allowlist as the Plan 4 route to
        # prevent injecting traversal segments into the X-Accel-Redirect URL.
        # safe_join will raise ParamsInvalid on any of `..`, absolute,
        # or non-allowlist names.
        safe_target = safe_join(cache_dir / f"lod{lod}", f"{stem}.webp")
        if not safe_target.exists():
            raise ThumbnailMissing(lod=lod, stem=stem)
        headers["X-Accel-Redirect"] = f"/_tiles_internal/{sha}/lod{lod}/{stem}.webp"
        return Response(status_code=200, headers=headers)

    # Legacy v0.2.15 layout — tiles live directly under 00_thumbnails/lodN/.
    cache = folder / "00_thumbnails"
    # safe_join validates EVERY part — the leading "lod{lod}" segment is
    # constructed server-side so we only need to validate `stem`.
    safe_stem = safe_join(cache / f"lod{lod}", f"{stem}.webp")
    if not safe_stem.exists():
        raise ThumbnailMissing(lod=lod, stem=stem)
    return FileResponse(str(safe_stem), media_type="image/webp", headers=headers)


@router.get("/static/raw/{filename}")
async def get_raw(
    project_id: str,
    filename: str,
    manifest: Manifest = Depends(get_manifest),
    user: User = Depends(get_current_user),
):
    """Pinned decision #3: raw served as-is (no transforms, no Y-flip)."""
    folder = Path(manifest.analysis_folder)
    raw_root = Path(json.loads((folder / "manifest.json").read_text())["raw_images_dir"])
    safe_path = safe_join(raw_root, filename)
    if not safe_path.exists():
        raise RawImageMissing(filename=filename)

    media_type, _ = mimetypes.guess_type(str(safe_path))
    if media_type is None:
        media_type = "application/octet-stream"

    headers = {
        "Cache-Control": "public, max-age=86400, immutable",
        "ETag": _thumb_etag(folder),  # share the thumbnails identity for now
    }
    return FileResponse(str(safe_path), media_type=media_type, headers=headers)
