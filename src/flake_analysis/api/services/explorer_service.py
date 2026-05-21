"""Explorer service: ports tab_explorer.py business logic to a stateless module.

Strict layering: routes call build_tile_manifest / build_flake_table /
build_flake_detail / resolve_raw_path. NO Streamlit imports.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from PIL import Image

from flake_analysis.api.errors import ParamsInvalid
from flake_analysis.api.schemas.explorer import (
    ExplorerFlakeDetail,
    TileManifest,
    TileManifestEntry,
)

_GRID_RE = re.compile(r"ix(\d+)_iy(\d+)")
_MAX_GRID = 60  # Pinned decision #7


def _read_manifest_json(folder: Path) -> dict[str, Any]:
    p = Path(folder) / "manifest.json"
    if not p.exists():
        raise FileNotFoundError(f"manifest.json not found in {folder}")
    return json.loads(p.read_text(encoding="utf-8"))


def _read_thumb_index(folder: Path) -> dict[str, Any]:
    p = Path(folder) / "00_thumbnails" / "index.json"
    if not p.exists():
        raise FileNotFoundError(f"00_thumbnails/index.json not found")
    return json.loads(p.read_text(encoding="utf-8"))


def _build_grid_layout(
    image_ids: list[int],
    image_id_to_name: dict[int, str],
) -> tuple[int, int, dict[int, tuple[int, int]]]:
    """Port of tab_explorer.py:_build_grid_layout (server-side Y-flip).

    iy=0 → row = grid_h - 1 (BOTTOM); iy=max → row = 0 (TOP).
    """
    coords: dict[int, tuple[int, int]] = {}
    parsed_all = True
    for iid in image_ids:
        name = image_id_to_name.get(int(iid), "")
        m = _GRID_RE.search(name) if name else None
        if m is None:
            parsed_all = False
            break
        coords[int(iid)] = (int(m.group(1)), int(m.group(2)))

    if parsed_all and coords:
        cols = [c for c, _ in coords.values()]
        rows = [r for _, r in coords.values()]
        grid_w = max(cols) - min(cols) + 1
        grid_h = max(rows) - min(rows) + 1
        cmin, rmax = min(cols), max(rows)
        coords = {iid: (c - cmin, rmax - r) for iid, (c, r) in coords.items()}
        return int(grid_w), int(grid_h), coords

    n = len(image_ids)
    grid_w = max(1, int(np.ceil(np.sqrt(n))))
    grid_h = max(1, int(np.ceil(n / grid_w)))
    fallback: dict[int, tuple[int, int]] = {}
    for i, iid in enumerate(image_ids):
        r, c = divmod(i, grid_w)
        fallback[int(iid)] = (c, r)
    return grid_w, grid_h, fallback


def build_tile_manifest(analysis_folder: str | Path) -> TileManifest:
    """Build the canonical tile manifest for the OSD mosaic.

    Per pinned decision #2: peek raw image size with PIL ONCE per stem.
    Per pinned decision #7: reject grids larger than 60×60.
    """
    folder = Path(analysis_folder)
    manifest = _read_manifest_json(folder)
    thumb_index = _read_thumb_index(folder)

    image_id_to_stem: dict[int, str] = {
        int(k): str(v) for k, v in manifest.get("image_id_to_stem", {}).items()
    }
    image_ids = sorted(image_id_to_stem.keys())
    grid_w, grid_h, coords = _build_grid_layout(image_ids, image_id_to_stem)

    if grid_w > _MAX_GRID or grid_h > _MAX_GRID:
        raise ParamsInvalid(
            reason="grid_too_large",
            grid_w=grid_w, grid_h=grid_h, max=_MAX_GRID,
        )

    raw_images_dir = Path(manifest["raw_images_dir"])
    lod_sizes: dict[str, list[int]] = {
        str(k): list(v) for k, v in thumb_index.get("lod_sizes", {}).items()
    }
    signature: list[str] = list(thumb_index.get("signature", []))
    params_hash: str = manifest.get("steps", {}).get("thumbnails", {}).get(
        "params_hash", ""
    )

    tiles: list[TileManifestEntry] = []
    for iid in image_ids:
        stem = image_id_to_stem[iid]
        col, row = coords[iid]
        raw_path = raw_images_dir / f"{stem}.png"
        if not raw_path.exists():
            # try common alternates
            for ext in (".jpg", ".jpeg", ".tif", ".tiff"):
                alt = raw_images_dir / f"{stem}{ext}"
                if alt.exists():
                    raw_path = alt
                    break
        with Image.open(raw_path) as im:
            w_px, h_px = im.size
        tiles.append(TileManifestEntry(
            image_id=iid,
            stem=stem,
            col=col,
            row=row,
            width_px=int(w_px),
            height_px=int(h_px),
            lod_sizes=lod_sizes,
        ))

    return TileManifest(
        grid_w=grid_w,
        grid_h=grid_h,
        lod_sizes=lod_sizes,
        signature=signature,
        params_hash=params_hash,
        tiles=tiles,
    )
