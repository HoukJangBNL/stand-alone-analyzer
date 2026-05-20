"""Explorer tab — Include/Exclude label picker + flake list + DetailPanel.

Combines ``04_clustering/labels.json`` + ``04_clustering/assignments.parquet``
+ ``05_domain_proximity/flake_assignments.parquet`` to provide an
interactive review of flakes (groups of touching domains) with:

* 3-column Include / Exclude / Available label picker
* NeighborFilter (size, isolation, border-clipped) — size active in PR 2.5
* 2x2 render toggles (Plan v34 defaults; rendering deferred to M3)
* 3-pane Z-layout: substrate raw-image mosaic · flake list · DetailPanel

Per plan v1 r9 §M2 PR 2.5 + §10 R9 spike. v0.2.15 swaps the
heatmap-style grid for a real WebP mosaic backed by the ``thumbnails``
LOD pyramid (``00_thumbnails/lod{0,1,2}/`` + raw fallback).

Mockup: ``06_tab_explorer.html``.
Qpress reference:
``.agents/tasks/standalone_flake_tool/qpress_explorer_reference.md``.

Bbox/outline overlays, Geometry+MaskStats sections are explicitly
deferred to M3 polish.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd
import streamlit as st

from flake_analysis.pipeline.explorer import save_explorer_state


# ─── Constants ───────────────────────────────────────────────────────────

# 10-color cluster palette (matches d3 category10; same as tab_clustering).
CLUSTER_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]

SELECTED_FLAKE_KEY = "explorer.selected_flake_id"
INCLUDE_KEY = "explorer.include"
EXCLUDE_KEY = "explorer.exclude"
TABLE_KEY = "exp_flake_table"

# Plan v34 default render toggles: flake bbox ON, flake outline OFF,
# island bbox OFF, island outline ON-thin.
TOGGLE_KEYS = (
    "exp_toggle_flake_bbox",
    "exp_toggle_flake_outline",
    "exp_toggle_island_bbox",
    "exp_toggle_island_outline",
)
TOGGLE_DEFAULTS = (True, False, False, True)


# ─── Data loading ────────────────────────────────────────────────────────

def _load_inputs(
    analysis_folder: str, annotations_path: str
) -> Optional[Dict[str, Any]]:
    """Load all required inputs. Returns None if any prereq missing.

    The ``flake_assignments.parquet`` written by flake-analysis-core has
    columns ``domain_id, flake_id, flake_size`` (no ``image_id``).
    When ``annotations.json`` is available, we join ``domain_id`` →
    ``image_id`` from the COCO ``annotations[]`` list. When it's missing,
    we fall back to ``image_id = 0`` for every domain.
    """
    af = Path(analysis_folder)
    labels_p = af / "04_clustering" / "labels.json"
    asn_p = af / "04_clustering" / "assignments.parquet"
    flakes_p = af / "05_domain_proximity" / "flake_assignments.parquet"
    if not (labels_p.exists() and asn_p.exists() and flakes_p.exists()):
        return None

    labels = json.loads(labels_p.read_text(encoding="utf-8"))
    assignments = pd.read_parquet(asn_p)
    flake_assignments = pd.read_parquet(flakes_p)

    # Normalize assignments column names (core uses cluster_label/max_posterior;
    # plan §7.1 reserves cluster_id/posterior_p — tolerate both).
    if "cluster_id" not in assignments.columns and "cluster_label" in assignments.columns:
        assignments = assignments.rename(columns={"cluster_label": "cluster_id"})
    if "posterior_p" not in assignments.columns and "max_posterior" in assignments.columns:
        assignments = assignments.rename(columns={"max_posterior": "posterior_p"})

    # Attach image_id to flake_assignments. Already present in synthetic
    # fixtures; for real outputs we read it from annotations.json.
    if "image_id" not in flake_assignments.columns:
        domain_to_image: Dict[int, int] = {}
        ann_p = Path(annotations_path) if annotations_path else None
        if ann_p is not None and ann_p.exists():
            try:
                coco = json.loads(ann_p.read_text(encoding="utf-8"))
                for ann in coco.get("annotations", []):
                    domain_to_image[int(ann["id"])] = int(ann["image_id"])
            except (OSError, ValueError, KeyError):
                domain_to_image = {}
        flake_assignments = flake_assignments.copy()
        flake_assignments["image_id"] = (
            flake_assignments["domain_id"]
            .astype(int)
            .map(domain_to_image)
            .fillna(0)
            .astype(int)
        )

    return {
        "labels": labels,
        "assignments": assignments,
        "flake_assignments": flake_assignments,
    }


def _build_flake_records(
    inputs: Dict[str, Any],
    include_labels: Set[str],
    exclude_labels: Set[str],
    neighbor_filter: Dict[str, Any],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Build per-flake summary table + apply filters.

    Returns ``(all_flakes_df, filtered_df)``. Columns:
    ``flake_id, image_id, domains, groups, distance, clipped, pass``.
    """
    fa: pd.DataFrame = inputs["flake_assignments"]
    asn: pd.DataFrame = inputs["assignments"]
    labels: Dict[str, Any] = inputs["labels"]

    # cluster_id → cluster_name + color (palette index = group order in labels)
    cid_to_name: Dict[int, str] = {
        int(g["id"]): g["name"] for g in labels.get("groups", [])
    }

    # domain_id → cluster_id (only for domains the GMM scored).
    asn_idx: Dict[int, int] = (
        asn.set_index("domain_id")["cluster_id"].astype(int).to_dict()
    )

    rows: List[Dict[str, Any]] = []
    for flake_id, group in fa.groupby("flake_id"):
        domain_ids = group["domain_id"].astype(int).tolist()
        cluster_ids: Set[int] = set()
        for d in domain_ids:
            cid = asn_idx.get(int(d))
            if cid is not None and cid >= 0:
                cluster_ids.add(int(cid))
        names = sorted(
            {cid_to_name.get(c, f"cluster_{c}") for c in cluster_ids}
        )
        image_id = int(group["image_id"].iloc[0]) if "image_id" in group.columns else 0
        rows.append({
            "flake_id": int(flake_id),
            "image_id": image_id,
            "domains": int(len(domain_ids)),
            "groups": ", ".join(names) if names else "—",
            "distance": "—",  # M3 polish: read from pair_distances.json
            "clipped": "no",   # M3 polish: read border_flakes from pair_distances.json
            "_cluster_set": frozenset(cluster_ids),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df, df

    # Resolve cluster names → cluster ids for filter lookup.
    name_to_cid: Dict[str, int] = {
        g["name"]: int(g["id"]) for g in labels.get("groups", [])
    }
    inc_ids: Optional[Set[int]] = (
        {name_to_cid[n] for n in include_labels if n in name_to_cid}
        if include_labels else None
    )
    exc_ids: Set[int] = {
        name_to_cid[n] for n in exclude_labels if n in name_to_cid
    }

    def _passes(cluster_set: frozenset) -> bool:
        # Include semantics: at least one member cluster must be in inc_ids
        # (when inc_ids is non-empty).
        if inc_ids is not None and inc_ids and not (cluster_set & inc_ids):
            return False
        # Exclude semantics: any member in exc_ids → reject.
        if exc_ids and (cluster_set & exc_ids):
            return False
        return True

    df["pass"] = df["_cluster_set"].apply(_passes)

    # NeighborFilter — size only is wired in PR 2.5; isolation/border are
    # session-state placeholders and deferred to M3 polish.
    if neighbor_filter.get("size_enabled"):
        smin = int(neighbor_filter.get("size_min", 1))
        smax = int(neighbor_filter.get("size_max", 50))
        df.loc[~df["domains"].between(smin, smax), "pass"] = False

    out = df.drop(columns=["_cluster_set"])
    filt = out.loc[out["pass"]].drop(columns=["pass"]).reset_index(drop=True)
    return out, filt


# ─── Filter section UI ───────────────────────────────────────────────────

def _render_label_picker(labels: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Sidebar-friendly Include / Exclude picker (vertical multiselect).

    Switched from a 3-column layout to two stacked multiselects so the
    picker fits the narrow ~280px sidebar drawer without label wrap.
    The "Available" column was redundant (each multiselect already
    shows the full label list in its dropdown).
    """
    all_names: List[str] = [g["name"] for g in labels.get("groups", [])]
    if not all_names:
        st.info("No clusters available. Commit clustering first.")
        return [], []

    include = st.multiselect(
        "Include",
        all_names,
        default=st.session_state.get(INCLUDE_KEY, []),
        key=INCLUDE_KEY,
    )
    avail_excl = [n for n in all_names if n not in include]
    prev_excl = [
        n for n in st.session_state.get(EXCLUDE_KEY, []) if n in avail_excl
    ]
    exclude = st.multiselect(
        "Exclude",
        avail_excl,
        default=prev_excl,
        key=EXCLUDE_KEY,
    )

    conflicts = sorted(set(include) & set(exclude))
    if conflicts:
        st.markdown(
            f"<span style='color:#C62828; font-style:italic;'>"
            f"Conflict: {conflicts} in both columns ignored</span>",
            unsafe_allow_html=True,
        )

    return include, exclude


def _render_neighbor_filter() -> Dict[str, Any]:
    """Compact vertical NeighborFilter for the sidebar drawer."""
    size_enabled = st.checkbox("Size range", value=False, key="exp_size_en")
    size_cols = st.columns(2)
    with size_cols[0]:
        size_min = st.number_input(
            "min", value=1, min_value=1, max_value=10000,
            key="exp_size_min", disabled=not size_enabled,
            label_visibility="collapsed",
        )
    with size_cols[1]:
        size_max = st.number_input(
            "max", value=50, min_value=1, max_value=10000,
            key="exp_size_max", disabled=not size_enabled,
            label_visibility="collapsed",
        )
    if size_enabled:
        st.caption("domains / flake")

    isolate_en = st.checkbox("Isolation ≥", value=False, key="exp_iso_en")
    d_isolate_px = st.number_input(
        "d_isolate_px", value=80.0, step=10.0,
        key="exp_iso_px", disabled=not isolate_en,
    )
    exclude_border = st.checkbox(
        "Exclude border-clipped flakes",
        value=False, key="exp_border",
    )

    return {
        "size_enabled": bool(size_enabled),
        "size_min": int(size_min),
        "size_max": int(size_max),
        "isolate_enabled": bool(isolate_en),
        "d_isolate_px": float(d_isolate_px),
        "exclude_border": bool(exclude_border),
    }


def _render_render_toggles() -> None:
    """Render toggles for the sidebar drawer (vertical, no expander).

    Pre-seed each widget key once with its default so Streamlit doesn't
    re-apply ``value=`` on later reruns and snap the toggle back to
    default after a rerun GC's the widget key.
    """
    for k, default in zip(TOGGLE_KEYS, TOGGLE_DEFAULTS):
        if k not in st.session_state:
            st.session_state[k] = bool(default)
    cols = st.columns(2)
    with cols[0]:
        st.checkbox("Flake bbox", key=TOGGLE_KEYS[0])
        st.checkbox("Flake outline", key=TOGGLE_KEYS[1])
    with cols[1]:
        st.checkbox("Island bbox", key=TOGGLE_KEYS[2])
        st.checkbox("Island outline", key=TOGGLE_KEYS[3])


# ─── Substrate canvas (raw-image mosaic) ─────────────────────────────────

# Mosaic chart pixel budget — kept in sync with go.Image y-axis range.
_MOSAIC_HEIGHT_PX: int = 500

# LOD width thresholds (1.5× the cached LOD widths so a cell never
# upscales a thumbnail by more than 1.5×). LOD 3 = raw image.
# Cached LODs come from ``core.pipeline.thumbnails.LOD_SIZES``.
_LOD_THRESHOLDS: Tuple[Tuple[int, int], ...] = (
    (96, 0),   # cell_px <= 96   → lod0  (64×40 thumb)
    (288, 1),  # cell_px <= 288  → lod1  (192×120 thumb)
    (720, 2),  # cell_px <= 720  → lod2  (480×300 thumb)
)
_RAW_LOD: int = 3

# ix###_iy###  (the leading "ix" tag identifies the column, "iy" the row).
_GRID_RE = re.compile(r"ix(\d+)_iy(\d+)")


def _parse_grid_coord(image_name: str) -> Optional[Tuple[int, int]]:
    """Return ``(col, row)`` parsed from filenames like ``ix003_iy017.png``.

    Returns ``None`` when the name doesn't match — caller falls back to a
    square ``divmod(i, sqrt(n))`` layout.
    """
    if not image_name:
        return None
    m = _GRID_RE.search(image_name)
    if m is None:
        return None
    try:
        col = int(m.group(1))
        row = int(m.group(2))
    except (TypeError, ValueError):
        return None
    return col, row


def _load_thumbnail_index(analysis_folder: str) -> Optional[Dict[str, Any]]:
    """Read ``00_thumbnails/index.json`` if present.

    Returns ``None`` when the file is missing or malformed — caller
    surfaces a "Run Compute → Thumbnails first" warning.
    """
    p = Path(analysis_folder) / "00_thumbnails" / "index.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _choose_lod(cell_px: int) -> int:
    """Map a per-cell pixel budget to a LOD index (0..3).

    Returns 3 for the implicit raw-image LOD.
    """
    for limit, lod in _LOD_THRESHOLDS:
        if cell_px <= limit:
            return lod
    return _RAW_LOD


def _pick_thumbnail_path(
    thumbnail_root: Path,
    raw_stem: str,
    lod: int,
    *,
    cache_dir: Optional[str] = None,
) -> Path:
    """Resolve the on-disk WebP path for a given raw stem + LOD.

    When ``cache_dir`` is provided (v0.2.16 local-cache redirect),
    thumbnails live under it instead of the analysis-folder
    ``00_thumbnails/`` root. ``cache_dir=None`` falls back to the
    legacy in-folder layout, so v0.2.15 caches keep working.
    """
    base = Path(cache_dir) if cache_dir else thumbnail_root
    return base / f"lod{lod}" / f"{raw_stem}.webp"


def _resolve_raw_path(
    raw_images_dir: str, raw_stem: str, raw_ext: str
) -> Path:
    return Path(raw_images_dir) / f"{raw_stem}{raw_ext}"


def _build_grid_layout(
    image_ids: List[int],
    image_id_to_name: Dict[int, str],
) -> Tuple[int, int, Dict[int, Tuple[int, int]]]:
    """Return ``(grid_w, grid_h, image_id → (col, row))``.

    Tries to parse ``ix###_iy###`` from each image's raw filename. If
    every name parses, the returned grid spans the actual coordinate
    extents.

    Y-axis convention (v0.2.17): ``iy=0`` is mapped to the **bottom**
    row of the mosaic — i.e. larger ``iy`` values render higher up.
    Row index in the returned mapping = ``(max_iy - iy)`` so the user-
    facing scan-grid origin matches their wafer convention. The chart
    y-axis still uses top-left = (0, 0) for plotly's image rendering;
    the row flip happens here so callers can stay axis-agnostic.

    If any filename fails to parse, falls back to the square
    ``divmod(i, sqrt(n))`` layout used in earlier versions.
    """
    coords: Dict[int, Tuple[int, int]] = {}
    parsed_all = True
    for iid in image_ids:
        name = image_id_to_name.get(int(iid), "")
        rc = _parse_grid_coord(name)
        if rc is None:
            parsed_all = False
            break
        coords[int(iid)] = rc

    if parsed_all and coords:
        cols = [c for c, _ in coords.values()]
        rows = [r for _, r in coords.values()]
        grid_w = (max(cols) - min(cols) + 1)
        grid_h = (max(rows) - min(rows) + 1)
        cmin, rmin = min(cols), min(rows)
        rmax = max(rows)
        # Normalise to (0..grid_w-1) for cols. Flip rows so that
        # iy=rmin lands at the BOTTOM of the mosaic (row = grid_h-1)
        # and iy=rmax lands at the top (row = 0). Cataloger scans
        # bottom-to-top in iy, so this matches the user's wafer view.
        coords = {
            iid: (c - cmin, rmax - r)
            for iid, (c, r) in coords.items()
        }
        return int(grid_w), int(grid_h), coords

    # Fallback: square layout in image_id sort order.
    n = len(image_ids)
    grid_w = max(1, int(np.ceil(np.sqrt(n))))
    grid_h = max(1, int(np.ceil(n / grid_w)))
    fallback: Dict[int, Tuple[int, int]] = {}
    for i, iid in enumerate(image_ids):
        r, c = divmod(i, grid_w)
        fallback[int(iid)] = (c, r)
    return grid_w, grid_h, fallback


@st.cache_data(show_spinner=False, max_entries=4)
def _build_mosaic_array(
    *,
    analysis_folder: str,
    raw_images_dir: str,
    raw_ext: str,
    lod: int,
    cell_w: int,
    cell_h: int,
    grid_w: int,
    grid_h: int,
    placement: Tuple[Tuple[int, int, int], ...],
    pass_image_ids: Tuple[int, ...],
    _cache_buster: str,
    cache_dir: Optional[str] = None,
) -> np.ndarray:
    """Assemble the ``(grid_h*cell_h, grid_w*cell_w, 3)`` RGB mosaic.

    ``placement`` is ``(image_id, col, row, raw_stem)``-tuples flattened
    to fit the ``@st.cache_data`` hashing requirements. ``raw_stem`` is
    derived from ``image_id_to_name`` upstream.

    Tiles whose ``image_id`` is **not** in ``pass_image_ids`` are
    desaturated to grayscale at 50% opacity blended with white so they
    visually recede behind the passing tiles.

    ``_cache_buster`` should be the ``thumbnails`` step ``completed_at``
    so re-running thumbnails invalidates the cache.
    """
    from PIL import Image

    pass_set = set(pass_image_ids)
    H, W = grid_h * cell_h, grid_w * cell_w
    canvas = np.full((H, W, 3), 255, dtype=np.uint8)
    thumbnail_root = Path(analysis_folder) / "00_thumbnails"

    # Reconstruct the placement tuples: stored as flat (iid, col, row, stem)
    # entries because @st.cache_data requires hashable inputs.
    for entry in placement:
        # entry is (iid, col, row, stem)
        iid_, col, row, stem = entry
        iid = int(iid_)
        if not stem:
            continue

        if lod >= _RAW_LOD:
            tile_path = _resolve_raw_path(raw_images_dir, stem, raw_ext)
        else:
            tile_path = _pick_thumbnail_path(
                thumbnail_root, stem, lod, cache_dir=cache_dir
            )

        try:
            img = Image.open(tile_path).convert("RGB")
            img = img.resize((cell_w, cell_h), Image.LANCZOS)
            arr = np.asarray(img, dtype=np.uint8)
        except Exception:
            # Missing tile → leave the white canvas in place.
            continue

        if iid not in pass_set:
            # Grayscale + 50% white blend → faded receding effect.
            gray = arr.mean(axis=2, keepdims=True)
            faded = (
                np.broadcast_to(gray, arr.shape) * 0.5 + 255 * 0.5
            ).astype(np.uint8)
            arr = faded

        y0 = row * cell_h
        x0 = col * cell_w
        canvas[y0:y0 + cell_h, x0:x0 + cell_w, :] = arr

    return canvas


def _render_substrate_grid(
    filtered: pd.DataFrame,
    all_df: pd.DataFrame,
    labels: Dict[str, Any],
    *,
    analysis_folder: str,
    raw_images_dir: str,
    annotations_path: str,
    manifest: Any,
) -> None:
    """Substrate raw-image mosaic, LOD-driven by chart cell size.

    Each tile is one ``image_id`` from the dataset, sourced from the
    pre-rendered LOD pyramid in ``00_thumbnails/`` (or the raw image
    when fully zoomed in). Tiles whose ``image_id`` has no passing
    flake under the current filter are dimmed + desaturated; the
    selected flake's tile gets a gold highlight. Click a tile to
    select the first flake in it.
    """
    import plotly.graph_objects as go

    if all_df.empty:
        st.info("No flakes to display.")
        return

    # Bail out early if the user hasn't generated thumbnails yet — the
    # mosaic depends on the LOD pyramid.
    thumb_index = _load_thumbnail_index(analysis_folder)
    if thumb_index is None:
        st.warning(
            "⚠ Thumbnails not generated. Run Compute → Thumbnails first."
        )
        return

    # Build image_id → raw filename stem map. Annotations.json is the
    # authoritative source for image_id ↔ file_name. Without it we
    # cannot resolve thumbnails, so warn and bail.
    image_id_to_name: Dict[int, str] = {}
    image_id_to_stem: Dict[int, str] = {}
    ann_p = Path(annotations_path) if annotations_path else None
    if ann_p is not None and ann_p.exists():
        try:
            coco = json.loads(ann_p.read_text(encoding="utf-8"))
            for img in coco.get("images", []):
                iid = int(img.get("id", -1))
                fn = str(img.get("file_name", ""))
                if iid >= 0 and fn:
                    image_id_to_name[iid] = fn
                    image_id_to_stem[iid] = Path(fn).stem
        except (OSError, ValueError, KeyError):
            image_id_to_name = {}
            image_id_to_stem = {}

    if not image_id_to_stem:
        st.warning(
            "⚠ Mosaic requires annotations.json image entries to map "
            "image_id → raw filename. Falling back to placeholder grid."
        )

    image_ids = sorted(int(i) for i in all_df["image_id"].unique().tolist())
    n_imgs = len(image_ids)

    # Layout — true (col, row) from filenames when available.
    grid_w, grid_h, coord_map = _build_grid_layout(image_ids, image_id_to_name)

    # Cell pixel budget = chart height / grid rows, capped at the chart
    # width / grid cols so tiles don't overflow horizontally either.
    # Microscopy tiles are landscape (w/h ≈ 1.6 — 1920×1200), so the
    # vertical budget usually dominates.
    cell_h_budget = max(1, _MOSAIC_HEIGHT_PX // max(1, grid_h))
    # Width-side budget assumes ~960 px chart width (Streamlit ~60% column).
    cell_w_budget = max(1, 960 // max(1, grid_w))
    cell_px_for_lod = max(cell_w_budget, cell_h_budget)
    auto_lod = _choose_lod(cell_px_for_lod)

    # LOD picker. Plotly's wheel/box zoom doesn't bubble back to
    # Streamlit (no relayout event), so we expose an explicit selector
    # so the user can manually drop into a higher-detail tier. ``Auto``
    # picks based on the current cell-px budget.
    #
    # Streamlit GC pattern: canonical store ``explorer.lod`` survives
    # mode-button reruns; widget key ``explorer_lod_choice_widget`` is
    # re-seeded from canonical on every render but only when missing
    # (post-GC rehydrate) so the user's just-submitted value isn't
    # clobbered (same pattern as the Selector axis pickers).
    lod_options = ["Auto", "lod0 (64×40)", "lod1 (192×120)", "lod2 (480×300)", "raw"]
    if "explorer.lod" not in st.session_state:
        st.session_state["explorer.lod"] = "Auto"
    if "explorer_lod_choice_widget" not in st.session_state:
        st.session_state["explorer_lod_choice_widget"] = (
            st.session_state["explorer.lod"]
        )
    lod_choice = st.selectbox(
        "Mosaic detail",
        lod_options,
        key="explorer_lod_choice_widget",
        help="Auto picks LOD by cell size. Override to force a tier "
             "(higher detail = slower, raw = read each image fresh). "
             "Forced tiers also enlarge the chart so the extra pixels "
             "are visible without zooming.",
    )
    st.session_state["explorer.lod"] = lod_choice
    if lod_choice == "Auto":
        lod = auto_lod
    elif lod_choice.startswith("lod0"):
        lod = 0
    elif lod_choice.startswith("lod1"):
        lod = 1
    elif lod_choice.startswith("lod2"):
        lod = 2
    else:
        lod = _RAW_LOD

    # Cell aspect ratio mirrors the LOD pyramid (8:5 = 1.6:1).
    # When the user manually picks a tier above Auto, scale the cell
    # up to that LOD's native size so the extra pixels are actually
    # visible. The chart height + width are also bumped (below) so
    # the larger cells aren't crammed into the default 500 px box.
    cell_aspect = 8 / 5
    if lod_choice != "Auto":
        lod_native_h = {0: 40, 1: 120, 2: 300, _RAW_LOD: 600}.get(lod, 40)
        cell_h = max(8, int(round(lod_native_h)))
    else:
        cell_h = max(8, int(round(cell_h_budget)))
    cell_w = max(8, int(round(cell_h * cell_aspect)))

    # Effective chart height = cell_h × grid_h (auto-fit). For Auto
    # mode we keep the legacy 500 px ceiling. For forced LODs we let
    # the chart grow up to a generous ceiling so the user can scroll
    # the page to inspect the bigger mosaic.
    if lod_choice == "Auto":
        effective_height = _MOSAIC_HEIGHT_PX
    else:
        # Cap at 4000 px to avoid runaway memory on huge grids.
        effective_height = min(4000, max(_MOSAIC_HEIGHT_PX, cell_h * grid_h))

    # Pass / fail per image_id.
    pass_set: Set[int] = set(filtered["flake_id"].astype(int).tolist())
    summary = (
        all_df.groupby("image_id")
        .apply(
            lambda d: pd.Series({
                "n_total": int(len(d)),
                "n_pass": int(d["flake_id"].astype(int).isin(pass_set).sum()),
            }),
            include_groups=False,
        )
        .reset_index()
    )
    summary_idx = summary.set_index("image_id")
    pass_image_ids: Tuple[int, ...] = tuple(
        sorted(
            int(iid)
            for iid in image_ids
            if int(summary_idx.loc[iid]["n_pass"]) > 0
        )
    )

    # Placement tuples for the cached mosaic build.
    placement: List[Tuple[int, int, int, str]] = []
    pos_to_image: Dict[Tuple[int, int], int] = {}
    for iid in image_ids:
        col, row = coord_map.get(iid, (0, 0))
        stem = image_id_to_stem.get(iid, "")
        placement.append((int(iid), int(col), int(row), stem))
        pos_to_image[(int(row), int(col))] = int(iid)

    cache_buster = ""
    try:
        thumb_step = manifest.steps.get("thumbnails")
        if thumb_step is not None and thumb_step.completed_at:
            cache_buster = str(thumb_step.completed_at)
    except Exception:
        cache_buster = ""

    # v0.2.16: when the thumbnails step redirected WebP writes to a
    # local-disk cache (network-mount projects), index.json carries
    # the absolute cache root. None ⇒ legacy in-folder layout.
    cache_dir_str: Optional[str] = thumb_index.get("cache_dir")

    mosaic = _build_mosaic_array(
        analysis_folder=analysis_folder,
        raw_images_dir=raw_images_dir,
        raw_ext=str(thumb_index.get("params", {}).get("raw_ext", ".png")),
        lod=int(lod),
        cell_w=int(cell_w),
        cell_h=int(cell_h),
        grid_w=int(grid_w),
        grid_h=int(grid_h),
        placement=tuple(placement),
        pass_image_ids=pass_image_ids,
        _cache_buster=cache_buster,
        cache_dir=cache_dir_str,
    )

    fig = go.Figure(data=go.Image(z=mosaic))

    # Hover info per cell — one invisible scatter point at each cell
    # center carries the tooltip text. ``go.Image`` itself doesn't
    # support per-pixel hovertemplates.
    hover_x: List[float] = []
    hover_y: List[float] = []
    hover_text: List[str] = []
    hover_iids: List[int] = []
    for iid in image_ids:
        col, row = coord_map.get(iid, (0, 0))
        s = summary_idx.loc[iid]
        n_total = int(s["n_total"])
        n_pass = int(s["n_pass"])
        cx = col * cell_w + cell_w / 2.0
        cy = row * cell_h + cell_h / 2.0
        hover_x.append(cx)
        hover_y.append(cy)
        hover_text.append(
            f"image {iid}<br>{n_pass}/{n_total} pass"
        )
        hover_iids.append(int(iid))

    fig.add_trace(
        go.Scatter(
            x=hover_x,
            y=hover_y,
            mode="markers",
            marker=dict(
                size=max(8, min(cell_w, cell_h) * 0.6),
                color="rgba(0,0,0,0)",
                line=dict(width=0),
            ),
            text=hover_text,
            customdata=hover_iids,
            hovertemplate="%{text}<extra></extra>",
            showlegend=False,
            name="tiles",
        )
    )

    # Selected-flake highlight: gold rectangle around its image's tile.
    sel_id = st.session_state.get(SELECTED_FLAKE_KEY)
    if sel_id is not None:
        match = all_df.loc[all_df["flake_id"] == int(sel_id)]
        if not match.empty:
            sel_img = int(match["image_id"].iloc[0])
            sel_col, sel_row = coord_map.get(sel_img, (-1, -1))
            if sel_col >= 0 and sel_row >= 0:
                x0 = sel_col * cell_w
                y0 = sel_row * cell_h
                fig.add_shape(
                    type="rect",
                    x0=x0, x1=x0 + cell_w,
                    y0=y0, y1=y0 + cell_h,
                    line=dict(color="#FFC800", width=3),
                    fillcolor="rgba(0,0,0,0)",
                )

    fig.update_layout(
        height=int(effective_height),
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(visible=False, range=[0, grid_w * cell_w]),
        yaxis=dict(
            visible=False,
            range=[grid_h * cell_h, 0],  # origin top-left
            scaleanchor="x",
            scaleratio=1,
        ),
        dragmode="pan",
    )

    # Enable wheel + modebar zoom. Default ``st.plotly_chart`` ships
    # with the modebar visible only on hover; we keep that but also
    # turn on ``scrollZoom`` so the wheel zooms in/out without needing
    # the toolbar. ``displaylogo=False`` removes the Plotly logo.
    chart_config = {
        "scrollZoom": True,
        "displayModeBar": True,
        "displaylogo": False,
        "modeBarButtonsToRemove": ["toImage", "autoScale2d", "lasso2d", "select2d"],
    }

    selection = st.plotly_chart(
        fig,
        config=chart_config,
        width="stretch",
        on_select="rerun",
        key="explorer_grid",
    )

    # On click: jump selection to the first flake whose image_id matches
    # the clicked tile.
    try:
        points = selection.get("selection", {}).get("points", []) if isinstance(
            selection, dict
        ) else (
            selection.selection.get("points", [])
            if hasattr(selection, "selection") and isinstance(selection.selection, dict)
            else []
        )
    except Exception:
        points = []

    if points:
        cd = points[0].get("customdata")
        clicked_iid: Optional[int] = None
        if isinstance(cd, list) and cd:
            try:
                clicked_iid = int(cd[0])
            except (TypeError, ValueError):
                clicked_iid = None
        elif isinstance(cd, (int, float)):
            clicked_iid = int(cd)
        if clicked_iid is not None:
            tile_flakes = all_df.loc[all_df["image_id"] == clicked_iid]
            if not tile_flakes.empty:
                st.session_state[SELECTED_FLAKE_KEY] = int(
                    tile_flakes.iloc[0]["flake_id"]
                )

    n_pass = int(len(filtered))
    n_total = int(len(all_df))
    lod_label = "raw" if lod >= _RAW_LOD else f"lod{lod}"
    st.caption(
        f"Substrate grid · LOD {lod_label} · {grid_w}×{grid_h} tiles · "
        f"{n_pass}/{n_total} flakes pass · click a tile to inspect"
    )
    _ = n_imgs  # noqa: F841 — retained for downstream debug hooks


# ─── Middle pane: flake list ─────────────────────────────────────────────

def _render_flake_list(filtered: pd.DataFrame) -> None:
    """Sortable flake list with row-click selection."""
    if filtered.empty:
        st.info("No flakes pass current filter.")
        return

    visible = filtered[
        ["flake_id", "image_id", "domains", "groups", "distance", "clipped"]
    ]

    # Streamlit 1.57: st.dataframe(on_select="rerun", selection_mode="single-row")
    # returns a DataframeState-like object via session_state[key].
    try:
        st.dataframe(
            visible,
            width="stretch",
            height=400,
            on_select="rerun",
            selection_mode="single-row",
            key=TABLE_KEY,
        )
        sel = st.session_state.get(TABLE_KEY, {})
        rows: List[int] = []
        if isinstance(sel, dict):
            rows = sel.get("selection", {}).get("rows", []) or []
        else:
            selection = getattr(sel, "selection", None)
            if selection is not None:
                rows = (
                    selection.get("rows", [])
                    if isinstance(selection, dict)
                    else getattr(selection, "rows", []) or []
                )
        if rows:
            idx = int(rows[0])
            if 0 <= idx < len(filtered):
                st.session_state[SELECTED_FLAKE_KEY] = int(
                    filtered.iloc[idx]["flake_id"]
                )
    except Exception as e:
        # Fallback: selectbox of flake_ids if interactive selection unsupported.
        st.warning(f"Row-click unavailable ({e}); using selectbox fallback.")
        st.dataframe(visible, width="stretch", height=400)
        opts = filtered["flake_id"].astype(int).tolist()
        chosen = st.selectbox(
            "Pick a flake_id",
            options=opts,
            key="exp_flake_pick",
        )
        if chosen is not None:
            st.session_state[SELECTED_FLAKE_KEY] = int(chosen)


# ─── Right pane: DetailPanel ─────────────────────────────────────────────

def _render_detail_panel(
    filtered: pd.DataFrame, labels: Dict[str, Any]
) -> None:
    """DetailPanel — Identity / Labels / Distance + empty-state legend."""
    sel_id = st.session_state.get(SELECTED_FLAKE_KEY)

    if sel_id is None or filtered.empty:
        st.subheader("Legend")
        st.markdown(
            "**Selection** · Gold rect on selected cell\n\n"
            "**Filter & Cells**\n"
            "- White halo: passes filter\n"
            "- 40% dim: rejected\n"
            "- Gold dual-stroke: selected\n\n"
            "**Group composition** · cluster colors (chips)\n\n"
            "**Special states** · noise = -1 (hidden)"
        )
        return

    matches = filtered.loc[filtered["flake_id"] == int(sel_id)]
    if matches.empty:
        st.warning(f"Flake {sel_id} not in current filter result.")
        return

    row = matches.iloc[0]
    st.subheader(f"Flake #{int(row['image_id'])}.{int(row['flake_id'])}")
    st.caption(f"image {int(row['image_id'])} · {int(row['domains'])} domains")

    st.markdown("**Identity**")
    st.write(f"Flake ID: {int(row['flake_id'])}")
    st.write(f"Image ID: {int(row['image_id'])}")
    st.write(f"Domain count: {int(row['domains'])}")

    st.markdown("**Labels**")
    name_to_color: Dict[str, str] = {}
    for i, g in enumerate(labels.get("groups", [])):
        name_to_color[g["name"]] = CLUSTER_PALETTE[i % len(CLUSTER_PALETTE)]
    if row["groups"] and row["groups"] != "—":
        chips = "".join(
            f"<span style='background:{name_to_color.get(name, '#9e9e9e')};"
            "color:white;padding:2px 8px;margin:2px;border-radius:10px;"
            f"font-size:12px;'>{name}</span>"
            for name in row["groups"].split(", ")
        )
        st.markdown(chips, unsafe_allow_html=True)
    else:
        st.caption("(no labels)")

    st.markdown("**Geometry**")
    st.caption(
        "BBox + dimensions: deferred to M3 polish "
        "(requires annotations.json + RLE decode)."
    )

    st.markdown("**Distance**")
    st.write(f"Nearest external: {row['distance']}")

    st.markdown("**Mask Stats**")
    st.caption("Total area / perimeter / centroid: deferred to M3 polish.")


# ─── Top-level renderer ──────────────────────────────────────────────────

def render_explorer_sidebar(
    labels: Dict[str, Any],
) -> Tuple[List[str], List[str], Dict[str, Any]]:
    """Render the Explorer control drawer in the sidebar.

    Mirrors the Selector / Clustering drawers (commit 1e74935 / da5df6c).
    Owns: include/exclude label picker, neighbor filter, render toggles.
    Save-state button stays in the tab body so it can use the
    just-computed filtered flake list.
    """
    include: List[str] = []
    exclude: List[str] = []
    neighbor_filter: Dict[str, Any] = {}
    with st.sidebar.expander("⚙ Explorer controls", expanded=True):
        st.caption("**Cluster picker**")
        include, exclude = _render_label_picker(labels)
        st.divider()
        st.caption("**Neighbor filter**")
        neighbor_filter = _render_neighbor_filter()
        st.divider()
        st.caption("**Render toggles**")
        _render_render_toggles()
    return include, exclude, neighbor_filter


def render_tab_explorer(
    raw_images_dir: str,
    annotations_path: str,
    analysis_folder: str,
) -> None:
    """Render the Explorer tab.

    Layout:
        sidebar drawer  ⚙ — cluster picker / neighbor filter / toggles
        body header    — clusters + filtered counts + group chips +
                         💾 Save explorer state
        body 3-pane    — substrate grid · flake list · DetailPanel
    """
    if not analysis_folder:
        st.warning("Set analysis_folder in sidebar to enable the Explorer tab.")
        return

    inputs = _load_inputs(analysis_folder, annotations_path)
    if inputs is None:
        st.warning(
            "⚠ Explorer requires Clustering and Domain Proximity to be committed."
        )
        return

    # Manifest carries the thumbnails ``completed_at`` used as the
    # mosaic cache buster (re-running thumbnails invalidates the cache).
    from flake_analysis.state.manifest import load_manifest as _load_manifest
    manifest = _load_manifest(analysis_folder)

    labels = inputs["labels"]
    n_groups = int(labels.get("n_clusters", 0))

    # Sidebar drawer (filters + toggles).
    include, exclude, neighbor_filter = render_explorer_sidebar(labels)

    all_df, filt_df = _build_flake_records(
        inputs, set(include), set(exclude), neighbor_filter
    )

    # Header banner: cluster count + filtered counts + per-group chips.
    n_total, n_pass = int(len(all_df)), int(len(filt_df))
    pct = (100.0 * n_pass / n_total) if n_total else 0.0
    st.success(
        f"✅ Explorer · {n_groups} clusters · "
        f"**{n_pass:,} / {n_total:,}** flakes pass current filter "
        f"({pct:.1f}%)"
    )

    if n_pass:
        counts: Dict[str, int] = {}
        for groups_str in filt_df["groups"]:
            if not groups_str or groups_str == "—":
                continue
            for name in groups_str.split(", "):
                counts[name] = counts.get(name, 0) + 1
        name_to_color = {
            g["name"]: CLUSTER_PALETTE[i % len(CLUSTER_PALETTE)]
            for i, g in enumerate(labels.get("groups", []))
        }
        chips = "".join(
            f"<span style='background:{name_to_color.get(n, '#9e9e9e')};"
            "color:white;padding:2px 8px;margin:2px;border-radius:10px;"
            f"font-size:12px;'>{n}: {c}</span>"
            for n, c in sorted(counts.items(), key=lambda kv: -kv[1])
        )
        st.markdown(chips, unsafe_allow_html=True)

    # Save-state action lives in the body so the user sees the
    # filtered count it'll persist before clicking.
    if st.button(
        "\U0001F4BE Save explorer state",
        type="primary",
        key="exp_save",
        help="Persist the include/exclude picks + filter state to "
             "06_explorer/explorer_state.json + the filtered flake "
             "list to selected_flakes.parquet.",
    ):
        try:
            save_explorer_state(
                analysis_folder=analysis_folder,
                include_labels=include,
                exclude_labels=exclude,
                neighbor_filter=neighbor_filter,
                selected_flake_ids=filt_df["flake_id"].astype(int).tolist(),
            )
            st.success("Explorer state saved.")
            st.rerun()
        except Exception as e:
            st.error(str(e))

    st.divider()

    # 3-pane Z-layout.
    left, mid, right = st.columns([60, 22, 18])
    with left:
        _render_substrate_grid(
            filt_df,
            all_df,
            labels,
            analysis_folder=analysis_folder,
            raw_images_dir=raw_images_dir,
            annotations_path=annotations_path,
            manifest=manifest,
        )
    with mid:
        st.subheader("Flakes")
        _render_flake_list(filt_df)
    with right:
        _render_detail_panel(filt_df, labels)
