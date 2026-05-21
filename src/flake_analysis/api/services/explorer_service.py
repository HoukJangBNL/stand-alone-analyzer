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


def _load_clustering_and_proximity(folder: Path) -> dict[str, Any]:
    labels_p = folder / "04_clustering" / "labels.json"
    asn_p = folder / "04_clustering" / "assignments.parquet"
    flakes_p = folder / "05_domain_proximity" / "flake_assignments.parquet"
    if not (labels_p.exists() and asn_p.exists() and flakes_p.exists()):
        raise FileNotFoundError("Clustering or domain_proximity output missing")

    labels = json.loads(labels_p.read_text(encoding="utf-8"))
    asn = pd.read_parquet(asn_p)
    fa = pd.read_parquet(flakes_p)

    # Tolerate both legacy column names.
    if "cluster_id" not in asn.columns and "cluster_label" in asn.columns:
        asn = asn.rename(columns={"cluster_label": "cluster_id"})
    if "posterior_p" not in asn.columns and "max_posterior" in asn.columns:
        asn = asn.rename(columns={"max_posterior": "posterior_p"})
    return {"labels": labels, "assignments": asn, "flake_assignments": fa}


def build_flake_table(
    analysis_folder: str | Path,
    *,
    include_labels: list[str],
    exclude_labels: list[str],
    size_min: Optional[int],
    size_max: Optional[int],
) -> pd.DataFrame:
    """Port of tab_explorer.py:_build_flake_records + server-side filter (pinned #4).

    Returns the FILTERED DataFrame (no `pass` column — only rows that pass).
    Columns: flake_id, image_id, domains, groups, distance, clipped, pass.
    """
    folder = Path(analysis_folder)
    inputs = _load_clustering_and_proximity(folder)
    fa: pd.DataFrame = inputs["flake_assignments"]
    asn: pd.DataFrame = inputs["assignments"]
    labels: dict[str, Any] = inputs["labels"]

    cid_to_name = {int(g["id"]): g["name"] for g in labels.get("groups", [])}
    asn_idx = asn.set_index("domain_id")["cluster_id"].astype(int).to_dict()

    rows: list[dict[str, Any]] = []
    for flake_id, group in fa.groupby("flake_id"):
        domain_ids = group["domain_id"].astype(int).tolist()
        cluster_ids: set[int] = set()
        for d in domain_ids:
            cid = asn_idx.get(int(d))
            if cid is not None and cid >= 0:
                cluster_ids.add(int(cid))
        names = sorted({cid_to_name.get(c, f"cluster_{c}") for c in cluster_ids})
        image_id = int(group["image_id"].iloc[0]) if "image_id" in group.columns else 0
        rows.append({
            "flake_id": int(flake_id),
            "image_id": image_id,
            "domains": int(len(domain_ids)),
            "groups": ", ".join(names) if names else "—",
            "distance": "—",
            "clipped": "no",
            "_cluster_set": frozenset(cluster_ids),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df.assign(**{"pass": pd.Series(dtype=bool)}).drop(columns=["_cluster_set"])

    name_to_cid = {g["name"]: int(g["id"]) for g in labels.get("groups", [])}
    inc_ids: Optional[set[int]] = (
        {name_to_cid[n] for n in include_labels if n in name_to_cid}
        if include_labels else None
    )
    exc_ids: set[int] = {name_to_cid[n] for n in exclude_labels if n in name_to_cid}

    def _passes(cset: frozenset) -> bool:
        if inc_ids is not None and inc_ids and not (cset & inc_ids):
            return False
        if exc_ids and (cset & exc_ids):
            return False
        return True

    df["pass"] = df["_cluster_set"].apply(_passes)
    if size_min is not None:
        df.loc[df["domains"] < size_min, "pass"] = False
    if size_max is not None:
        df.loc[df["domains"] > size_max, "pass"] = False

    out = df.drop(columns=["_cluster_set"])
    return out.loc[out["pass"]].reset_index(drop=True)
