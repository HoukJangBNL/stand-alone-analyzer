"""Clustering tab — manual seed-group GMM + per-cluster thresholds.

Operates on the selector-narrowed domain set. v0.1.2 swapped the inline
brushing helpers for the shared ``_brushing`` module so mode toggles
(Replace/Add/Subtract), undo/redo, and scroll-zoom are uniform with the
Selector tab. Per-domain image preview is intentionally out of scope for
this tab in v0.1.2 (selection here drives seed-group authoring, not
inspection).

Per plan v1 r9 §M2 PR 2.4. Mockup: ``05_tab_clustering.html``.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import numpy as np
import pandas as pd
import streamlit as st

from flake_analysis.pipeline.clustering import apply_thresholds, run_clustering_step
from flake_analysis.state.manifest import load_manifest
from flake_analysis.ui import _brushing
from flake_analysis.ui._image_preview import render_image_preview
from flake_analysis.ui.tab_selector import (
    AVAILABLE_AXES,
    _focus_domain_id,
    _values_for_axis,
)


# ─── Constants ───────────────────────────────────────────────────────────

# 10-color cluster palette (matches plotly d3 category10).
CLUSTER_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]
NEUTRAL_GRAY = "#9e9e9e"

SEED_GROUPS_KEY = "clustering.seed_groups"

_MAX_POINTS = 5000


# ─── Session state helpers ───────────────────────────────────────────────

def _ensure_session_seed_groups() -> List[Dict[str, Any]]:
    if SEED_GROUPS_KEY not in st.session_state:
        st.session_state[SEED_GROUPS_KEY] = []
    return st.session_state[SEED_GROUPS_KEY]


# Sentinel: tracks which analysis_folder we already auto-loaded the
# disk seed groups for, so swapping projects auto-loads again but
# editing-then-revisiting doesn't clobber the user's in-progress edits.
_AUTOLOAD_KEY = "clustering.seed_groups_autoloaded_for"


def _maybe_autoload_seed_groups(analysis_folder: str) -> bool:
    """If the session has no seed groups yet, hydrate from disk.

    Returns True when an auto-load actually happened (so the caller
    can rerun and reflect the new state). Idempotent per
    ``analysis_folder`` — switching projects re-arms the autoload,
    revisiting the same project doesn't overwrite in-flight edits.
    """
    last = st.session_state.get(_AUTOLOAD_KEY)
    if last == analysis_folder:
        return False
    current = st.session_state.get(SEED_GROUPS_KEY)
    if current:
        # User already has unsaved edits — don't clobber. Mark this
        # folder as "handled" so we won't re-attempt next rerun.
        st.session_state[_AUTOLOAD_KEY] = analysis_folder
        return False
    disk = _load_committed_seed_groups(analysis_folder)
    st.session_state[_AUTOLOAD_KEY] = analysis_folder
    if not disk:
        return False
    st.session_state[SEED_GROUPS_KEY] = list(disk)
    return True


# ─── Data loading ────────────────────────────────────────────────────────

def _load_inputs(analysis_folder: str) -> Optional[Dict[str, Any]]:
    """Return dict with full stats arrays + selected_mask, or None if missing.

    Exposes ``areas``, ``std_pcts``, ``sam2`` in addition to the colour
    info so the configurable 2D scatter (which reuses Selector's axis
    options) can plot any pair of metrics on demand.
    """
    stats_path = Path(analysis_folder) / "02_domain_stats" / "stats.npz"
    sel_path = Path(analysis_folder) / "03_selector" / "selection.parquet"
    if not stats_path.exists() or not sel_path.exists():
        return None
    npz = np.load(stats_path, allow_pickle=False)
    sel_df = pd.read_parquet(sel_path)

    flake_ids = npz["flake_ids"].astype(np.int64)
    sel_set = set(sel_df.loc[sel_df["selected"].astype(bool), "domain_id"].astype(int).tolist())
    selected_mask = np.isin(flake_ids, list(sel_set))

    out: Dict[str, Any] = {
        "flake_ids": flake_ids,
        "repr_rgbs": npz["repr_rgbs"],
        "std_pcts": npz["std_pcts"] if "std_pcts" in npz.files else None,
        "areas": npz["areas"] if "areas" in npz.files else None,
        "sam2": npz["sam2"] if "sam2" in npz.files else None,
        "selected_mask": selected_mask,
        "sel_count": int(selected_mask.sum()),
    }
    return out


def _load_committed_clustering(analysis_folder: str) -> Optional[Dict[str, Any]]:
    """Return labels.json contents if committed, else None."""
    p = Path(analysis_folder) / "04_clustering" / "labels.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _load_committed_assignments(
    analysis_folder: str,
) -> Optional[pd.DataFrame]:
    """Return the per-domain assignment + max_posterior table.

    Used to drive the live threshold preview: for each cluster we know
    which domain ids it currently owns and each owner's max posterior,
    so the user can see immediately how a threshold change shrinks /
    grows that cluster.
    """
    p = Path(analysis_folder) / "04_clustering" / "assignments.parquet"
    if not p.exists():
        return None
    return pd.read_parquet(p)


def _load_committed_seed_groups(
    analysis_folder: str,
) -> Optional[List[Dict[str, Any]]]:
    """Return the seed groups persisted by the last Fit GMM, or None."""
    p = Path(analysis_folder) / "04_clustering" / "seed_groups.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _seeds_from_assignments(
    labels: Dict[str, Any],
    assignments_df: pd.DataFrame,
    live_thresholds: Dict[int, float],
) -> List[Dict[str, Any]]:
    """Convert the committed cluster assignment into seed groups.

    Useful when the user wants to iterate: take the post-fit clustering
    (filtered by the current live thresholds), feed it back as fresh
    seed groups for the next Fit. Each cluster becomes one named group
    using the names from ``labels.groups``.
    """
    groups_meta = labels.get("groups", [])
    id_to_name = {int(g["id"]): str(g.get("name", f"cluster {g['id']}")) for g in groups_meta}

    out: List[Dict[str, Any]] = []
    for cid in sorted(id_to_name):
        thresh = float(live_thresholds.get(cid, 0.5))
        sub = assignments_df[
            (assignments_df["cluster_label"] == cid)
            & (assignments_df["max_posterior"] >= thresh)
        ]
        ids = sorted(int(d) for d in sub["domain_id"].tolist())
        if ids:
            out.append({"name": id_to_name[cid], "domain_ids": ids})
    return out


def _downsample_indices(
    n: int,
    *,
    flake_ids: Optional[np.ndarray] = None,
    must_include_ids: Optional[Set[int]] = None,
    cap: int = _MAX_POINTS,
) -> np.ndarray:
    """Pick up to ``cap`` indices, but always keep ``must_include_ids``.

    Mirrors the helper in tab_selector — selected domains stay visible
    even when the dataset exceeds the downsample cap.
    """
    if n <= cap:
        return np.arange(n)
    rng = np.random.default_rng(0)
    base = rng.choice(n, cap, replace=False)
    if must_include_ids and flake_ids is not None and len(must_include_ids) > 0:
        keep_mask = np.isin(flake_ids, list(must_include_ids))
        keep_idx = np.where(keep_mask)[0]
        if keep_idx.size:
            combined = np.unique(np.concatenate([base, keep_idx]))
            if combined.size > cap:
                non_keep = np.setdiff1d(base, keep_idx, assume_unique=False)
                space = max(cap - keep_idx.size, 0)
                base = np.concatenate([keep_idx, non_keep[:space]])
                return np.sort(base)
            return np.sort(combined)
    return np.sort(base)


# ─── Seed group panel ────────────────────────────────────────────────────

def _seed_groups_to_table(
    seed_groups: List[Dict[str, Any]],
    rgb: np.ndarray,
    ids: np.ndarray,
) -> pd.DataFrame:
    """Per-row seed group summary with the palette colour leading.

    Columns: ``color`` (hex code from :data:`CLUSTER_PALETTE`, matched
    to the scatter so the user can match a row to its visual cluster),
    ``name``, ``count``, ``mean_r/g/b``. The colour comes from the row
    index (matches :func:`_seed_colors_for_ids`) so the dataframe and
    scatter agree.
    """
    rows: List[Dict[str, Any]] = []
    for g_idx, g in enumerate(seed_groups):
        color = CLUSTER_PALETTE[g_idx % len(CLUSTER_PALETTE)]
        ids_g = list(g.get("domain_ids", []))
        if ids_g:
            mask = np.isin(ids, ids_g)
            idx = np.where(mask)[0]
        else:
            idx = np.array([], dtype=np.int64)
        if idx.size > 0:
            mean_rgb = rgb[idx].mean(axis=0)
            rows.append({
                "color": color,
                "name": g.get("name", "?"),
                "count": int(len(ids_g)),
                "mean_r": round(float(mean_rgb[0]), 3),
                "mean_g": round(float(mean_rgb[1]), 3),
                "mean_b": round(float(mean_rgb[2]), 3),
            })
        else:
            rows.append({
                "color": color,
                "name": g.get("name", "?"),
                "count": int(len(ids_g)),
                "mean_r": 0.0,
                "mean_g": 0.0,
                "mean_b": 0.0,
            })
    return pd.DataFrame(rows)


def _render_seed_group_panel(
    stats: Dict[str, Any],
    state: _brushing.BrushingState,
    *,
    analysis_folder: str = "",
    labels: Optional[Dict[str, Any]] = None,
    assignments_df: Optional[pd.DataFrame] = None,
    live_thresholds: Optional[Dict[int, float]] = None,
) -> None:
    """Seed group management UI — vertical layout for the sidebar drawer.

    Three small sections (Add / Edit / Clear), each on its own row so
    labels don't collide in the narrow ~280px drawer width. The summary
    table moves to the tab body where there's room for it; the sidebar
    just shows a compact "N groups, M total domains" caption.
    """
    seed_groups = _ensure_session_seed_groups()
    selected_ids = state.selected_ids

    if seed_groups:
        n_groups = len(seed_groups)
        n_total = sum(len(g.get("domain_ids", [])) for g in seed_groups)
        st.caption(f"**{n_groups}** group(s) · **{n_total:,}** total domain(s)")
    else:
        st.caption("No seed groups yet. Lasso domains then click + Add.")

    st.caption(
        f"Lasso buffer: **{len(selected_ids):,}** domain(s) "
        f"(mode={state.mode})"
    )

    # Add row.
    new_name = st.text_input(
        "New group name",
        key="cluster_new_name",
        placeholder="e.g. graphite",
    )
    if st.button("+ Add group", key="cluster_add_group", use_container_width=True):
        if not new_name:
            st.warning("Enter a group name first.")
        elif not selected_ids:
            st.warning("Lasso some domains first.")
        elif any(g.get("name") == new_name for g in seed_groups):
            st.warning(f"Group '{new_name}' already exists.")
        else:
            seed_groups.append({
                "name": new_name,
                "domain_ids": sorted(int(i) for i in selected_ids),
            })
            _brushing.clear_selection(state)
            st.rerun()

    # Edit row (rename / remove a target group).
    names = [g["name"] for g in seed_groups]
    if names:
        target = st.selectbox(
            "Edit group",
            names,
            key="cluster_target_group",
        )
        new_n = st.text_input(
            "Rename to",
            key="cluster_rename_new",
            placeholder="(leave blank to remove)",
        )
        edit_cols = st.columns(2)
        with edit_cols[0]:
            if st.button(
                "✏ Rename",
                key="cluster_rename_btn",
                use_container_width=True,
                disabled=not new_n,
            ):
                for g in seed_groups:
                    if g["name"] == target:
                        g["name"] = new_n
                        break
                st.rerun()
        with edit_cols[1]:
            if st.button(
                "− Remove",
                key="cluster_remove_group",
                use_container_width=True,
            ):
                seed_groups[:] = [
                    g for g in seed_groups if g["name"] != target
                ]
                st.rerun()
    else:
        st.caption("(add a group to enable edit / remove)")

    if st.button(
        "↺ Clear all groups",
        key="cluster_clear_all",
        use_container_width=True,
        disabled=not seed_groups,
    ):
        seed_groups.clear()
        _brushing.clear_selection(state)
        st.rerun()

    # Disk re-load + cluster import.
    has_disk_seeds = (
        analysis_folder
        and (Path(analysis_folder) / "04_clustering" / "seed_groups.json").exists()
    )
    can_import_clusters = (
        labels is not None
        and assignments_df is not None
        and "max_posterior" in (assignments_df.columns if assignments_df is not None else [])
    )
    if has_disk_seeds or can_import_clusters:
        st.caption("From disk:")
    if has_disk_seeds:
        if st.button(
            "↻ Reload last fit's seeds",
            key="cluster_reload_seeds",
            help="Replace current seed groups with the seeds saved by "
                 "the last Fit GMM (04_clustering/seed_groups.json).",
            use_container_width=True,
        ):
            disk = _load_committed_seed_groups(analysis_folder) or []
            st.session_state[SEED_GROUPS_KEY] = list(disk)
            _brushing.clear_selection(state)
            st.rerun()
    if can_import_clusters:
        if st.button(
            "⇪ Import clusters → seeds",
            key="cluster_import_clusters",
            help="Replace current seed groups with the post-fit cluster "
                 "members (filtered by live thresholds). Lets you iterate: "
                 "fit, threshold, then re-fit on the cleaned-up clusters.",
            use_container_width=True,
        ):
            new_seeds = _seeds_from_assignments(
                labels or {}, assignments_df, live_thresholds or {},
            )
            st.session_state[SEED_GROUPS_KEY] = new_seeds
            _brushing.clear_selection(state)
            st.rerun()


# ─── 4-pane scatter ──────────────────────────────────────────────────────

def _dispatch_event(event, state: _brushing.BrushingState) -> bool:
    """Route a Plotly chart event based on the active interaction mode.

    Single-pick replaces with the clicked id (clears focus_id since
    Clustering tab has no image preview, but we keep the field consistent
    with Selector). Lasso uses the mode-aware combine.
    """
    if state.interaction_mode == _brushing.INTERACTION_SINGLE:
        if _brushing.handle_click_event(event, state):
            state.focus_id = None
            return True
        return False
    if state.interaction_mode == _brushing.INTERACTION_ZOOM:
        # Zoom mode: Plotly handles the viewport change; no selection events.
        return False
    return _brushing.handle_selection_event(event, state)


def _on_clu_x_axis_change() -> None:
    st.session_state["clu_axis.x"] = st.session_state["clu_x_axis"]


def _on_clu_y_axis_change() -> None:
    st.session_state["clu_axis.y"] = st.session_state["clu_y_axis"]


def _on_clu_show_3d_change() -> None:
    st.session_state["clu.show_3d"] = bool(st.session_state["clu_show_3d"])


def _render_clu_axis_pickers() -> tuple[str, str]:
    """X / Y axis dropdowns for the Clustering 2D scatter.

    Mirrors :func:`tab_selector._render_axis_pickers` (canonical store +
    on_change callback pattern that survives Streamlit's GC of widget
    keys after a mode-button rerun) but uses ``clu_*`` keys so the two
    tabs can have independent picks.
    """
    if "clu_axis.x" not in st.session_state:
        st.session_state["clu_axis.x"] = AVAILABLE_AXES[0]
    if "clu_axis.y" not in st.session_state:
        st.session_state["clu_axis.y"] = AVAILABLE_AXES[1]
    if "clu_x_axis" not in st.session_state:
        st.session_state["clu_x_axis"] = st.session_state["clu_axis.x"]
    if "clu_y_axis" not in st.session_state:
        st.session_state["clu_y_axis"] = st.session_state["clu_axis.y"]

    pick_cols = st.columns(2)
    with pick_cols[0]:
        x = st.selectbox(
            "X-axis",
            AVAILABLE_AXES,
            key="clu_x_axis",
            on_change=_on_clu_x_axis_change,
        )
    with pick_cols[1]:
        y = st.selectbox(
            "Y-axis",
            AVAILABLE_AXES,
            key="clu_y_axis",
            on_change=_on_clu_y_axis_change,
        )
    return x, y


def _build_clu_scatter_arrays(
    stats: Dict[str, Any],
    state: _brushing.BrushingState,
    x_axis: str,
    y_axis: str,
) -> Optional[Dict[str, np.ndarray]]:
    """Selector-narrowed + downsampled arrays for the Clustering scatter.

    Returns ``None`` when the selector-committed set is empty (caller
    shows an info message instead of a blank chart).
    """
    flake_ids_all = stats["flake_ids"].astype(np.int64)
    sel_mask = stats["selected_mask"]
    n_total = int(len(flake_ids_all))

    visible_idx = np.where(sel_mask)[0]
    n_visible = int(len(visible_idx))
    if n_visible == 0:
        return None

    flake_ids = flake_ids_all[visible_idx]
    x_full = _values_for_axis(stats, x_axis)[visible_idx]
    y_full = _values_for_axis(stats, y_axis)[visible_idx]
    rgb_visible = stats["repr_rgbs"][visible_idx]

    sub_idx = _downsample_indices(
        n_visible,
        flake_ids=flake_ids,
        must_include_ids=state.selected_ids,
    )
    return {
        "x_sub": x_full[sub_idx],
        "y_sub": y_full[sub_idx],
        "ids_sub": flake_ids[sub_idx],
        "rgb_sub": rgb_visible[sub_idx],
        "n_total": np.int64(n_total),
        "n_visible": np.int64(n_visible),
    }


def _cluster_colors_for_ids(
    ids: np.ndarray, cluster_assign: Optional[Dict[int, int]]
) -> np.ndarray:
    """Map each domain id to its cluster colour (or neutral gray)."""
    if cluster_assign:
        return np.array([
            CLUSTER_PALETTE[cluster_assign[int(fid)] % len(CLUSTER_PALETTE)]
            if int(fid) in cluster_assign and cluster_assign[int(fid)] >= 0
            else NEUTRAL_GRAY
            for fid in ids
        ])
    return np.full(len(ids), NEUTRAL_GRAY)


def _combine_seed_and_cluster_colors(
    ids: np.ndarray,
    seed_groups: List[Dict[str, Any]],
    cluster_assign: Optional[Dict[int, int]],
) -> np.ndarray:
    """Seed-group colour wins; otherwise cluster colour; else gray.

    Seed colour priority resolves the "color column doesn't match the
    scatter" confusion when a previous Fit GMM result still lives on
    disk: the user-defined groups are the source of truth for the
    swatch table, so they should also drive the scatter colours.
    Cluster colours fill in the rest of the canvas as ambient
    information.
    """
    seed_arr = _seed_colors_for_ids(ids, seed_groups)
    if cluster_assign:
        cluster_arr = _cluster_colors_for_ids(ids, cluster_assign)
        # Where seed says "neutral" (not a member of any group), fall
        # back to cluster colour. NEUTRAL_GRAY string compare is the
        # marker for "no group".
        return np.where(seed_arr == NEUTRAL_GRAY, cluster_arr, seed_arr)
    return seed_arr


def _live_cluster_assign(
    labels: Dict[str, Any],
    assignments_df: Optional[pd.DataFrame],
    live_thresholds: Dict[int, float],
) -> Dict[int, int]:
    """Apply ``live_thresholds`` to assignments to build a preview map.

    Domains whose ``max_posterior`` is below the cluster's live
    threshold get cluster_id ``-1`` (= neutral gray on the scatter)
    so the user sees how shrinking / growing the threshold reshapes
    the colour map without committing to disk. Falls back to the
    plain assignments dict from ``labels`` when the parquet is not
    available (older analyses).
    """
    if assignments_df is None or "max_posterior" not in assignments_df.columns:
        return {
            int(k): int(v)
            for k, v in (labels.get("assignments") or {}).items()
        }
    out: Dict[int, int] = {}
    for _, row in assignments_df.iterrows():
        cid = int(row["cluster_label"])
        if cid < 0:
            out[int(row["domain_id"])] = cid
            continue
        thresh = float(live_thresholds.get(cid, 0.5))
        out[int(row["domain_id"])] = (
            cid if float(row["max_posterior"]) >= thresh else -1
        )
    return out


def _seed_colors_for_ids(
    ids: np.ndarray, seed_groups: List[Dict[str, Any]]
) -> np.ndarray:
    """Colour each domain id by its seed-group membership.

    Each named group gets its slot in :data:`CLUSTER_PALETTE` (so the
    pre-fit seed colours and the post-fit cluster colours are
    visually consistent). Domains not in any group stay neutral gray.
    The first group keyed by name (sorted by insertion order) gets
    palette[0] etc.; if a domain appears in multiple groups (rare,
    user-error case), the LAST group wins so the visible colour
    matches the table's "owning" row.
    """
    color_arr = np.full(len(ids), NEUTRAL_GRAY, dtype=object)
    if not seed_groups:
        return color_arr
    id_to_idx = {int(fid): i for i, fid in enumerate(ids)}
    for g_idx, g in enumerate(seed_groups):
        c = CLUSTER_PALETTE[g_idx % len(CLUSTER_PALETTE)]
        for d in g.get("domain_ids", []):
            i = id_to_idx.get(int(d))
            if i is not None:
                color_arr[i] = c
    return color_arr


def _edit_group_member_ids(
    seed_groups: List[Dict[str, Any]],
) -> Set[int]:
    """Domain ids belonging to the group currently picked in the
    "Edit group" selectbox. Empty set when no group is picked or the
    selectbox hasn't been rendered yet."""
    target = st.session_state.get("cluster_target_group")
    if not target:
        return set()
    for g in seed_groups:
        if g.get("name") == target:
            return {int(d) for d in g.get("domain_ids", [])}
    return set()


def _render_clu_2d_scatter(
    arrays: Dict[str, np.ndarray],
    state: _brushing.BrushingState,
    cluster_assign: Optional[Dict[int, int]],
    seed_groups: List[Dict[str, Any]],
    edit_group_ids: Set[int],
    x_axis: str,
    y_axis: str,
    *,
    height: int = 520,
) -> None:
    """Configurable 2D scatter for the Clustering tab body.

    Colour priority: cluster assignment (post-Fit) → seed-group
    membership (pre-Fit) → neutral gray. Highlight ring (orange) goes
    on the union of the user's lasso brush and the currently picked
    "Edit group" so the user can see exactly which points belong to
    the group they're about to rename / remove.
    """
    x_sub = arrays["x_sub"]
    y_sub = arrays["y_sub"]
    ids_sub = arrays["ids_sub"]
    n_total = int(arrays["n_total"])
    n_visible = int(arrays["n_visible"])

    base_colors = _combine_seed_and_cluster_colors(
        ids_sub, seed_groups, cluster_assign,
    )

    if n_total > n_visible:
        st.caption(
            f"Showing {n_visible:,} accepted of {n_total:,} domains "
            f"(rest filtered out by selector commit)."
        )
    if n_visible > _MAX_POINTS:
        st.caption(
            f"Downsampled to {_MAX_POINTS:,} of {n_visible:,} accepted "
            f"domains (selected ids always kept)."
        )

    # Union: lasso brush + edit-group members → orange highlight ring.
    highlight = set(state.selected_ids) | set(edit_group_ids)
    dragmode = _brushing.get_dragmode(state)
    interaction = state.interaction_mode
    if interaction == _brushing.INTERACTION_SINGLE:
        pane_hint = "click to select · drag to pan · scroll to zoom"
    elif interaction == _brushing.INTERACTION_ZOOM:
        pane_hint = "drag a box to zoom in · scroll to zoom · double-click resets"
    else:
        pane_hint = "lasso to brush · scroll to zoom"

    suffix = f"{interaction}_{x_axis}_{y_axis}"
    st.caption(f"{x_axis} vs {y_axis} ({pane_hint})")
    fig = _brushing.make_2d_scatter(
        x_sub, y_sub, ids_sub,
        base_colors=base_colors, selected_ids=highlight,
        x_label=x_axis, y_label=y_axis,
        height=height,
        dragmode=dragmode,
    )
    evt = _brushing.render_scatter(
        fig, key=f"clu_pane_xy_{suffix}", interaction_mode=interaction,
    )
    if _dispatch_event(evt, state):
        st.rerun()


def _render_clu_3d_rgb(
    arrays: Dict[str, np.ndarray],
    state: _brushing.BrushingState,
    cluster_assign: Optional[Dict[int, int]],
    seed_groups: List[Dict[str, Any]],
    edit_group_ids: Set[int],
    *,
    height: int = 520,
) -> None:
    """3D R-G-B context pane (display only). Same colour / highlight
    logic as the 2D scatter."""
    rgb_sub = arrays["rgb_sub"]
    ids_sub = arrays["ids_sub"]
    base_colors = _combine_seed_and_cluster_colors(
        ids_sub, seed_groups, cluster_assign,
    )
    highlight = set(state.selected_ids) | set(edit_group_ids)
    st.caption("3D R-G-B (display only)")
    fig3d = _brushing.make_3d_scatter(
        rgb_sub, ids_sub,
        base_colors=base_colors, selected_ids=highlight,
        height=height,
    )
    _brushing.render_scatter(fig3d, key="clu_pane_3d", on_select=False)


# ─── Diagnostics + threshold sliders + cluster size chart ────────────────

def _render_diagnostics(result: Dict[str, Any]) -> None:
    """r7 mapping diagnostics — visible only when n_dropped_* > 0."""
    n_seed = int(result.get("n_dropped_seed_ids", 0))
    n_sel = int(result.get("n_dropped_selected_ids", 0))
    if n_seed == 0 and n_sel == 0:
        return
    lines = ["**Mapping diagnostics:**"]
    if n_seed > 0:
        lines.append(
            f"- ⚠ {n_seed} seed domain_ids no longer in selector (dropped)"
        )
    if n_sel > 0:
        lines.append(
            f"- ⚠ {n_sel} NPZ entries skipped (no longer in selector)"
        )
    st.warning("\n".join(lines))


def _render_per_cluster_thresholds_sidebar(
    labels: Dict[str, Any],
    assignments_df: Optional[pd.DataFrame],
) -> Dict[int, float]:
    """Threshold sliders inside the sidebar drawer.

    Returns the live ``{cluster_id: threshold}`` map so the caller can
    apply it to the scatter colour preview *immediately* (no Apply
    button needed for the visual). Each slider also reports a live
    "K / N pass" caption derived from ``assignments_df.max_posterior``
    so the user sees the count change as they drag.

    Disk persistence stays a separate explicit action (the Apply
    button in :func:`_render_threshold_apply_button`) so the user can
    explore freely without overwriting the committed labels.
    """
    n_clusters = int(labels.get("n_clusters", 0))
    if n_clusters == 0:
        st.caption("No clusters yet — Fit GMM first.")
        return {}

    current_thresh = labels.get("thresholds", {})
    groups = labels.get("groups", [])

    # Pre-seed canonical store + widget keys (mode-button rerun safe,
    # same pattern as the axis pickers).
    for group in groups:
        cid = int(group["id"])
        store = f"clu.thresh.{cid}"
        widget = f"clu_thresh_{cid}"
        if store not in st.session_state:
            st.session_state[store] = float(current_thresh.get(str(cid), 0.50))
        if widget not in st.session_state:
            st.session_state[widget] = float(st.session_state[store])

    new_thresh: Dict[int, float] = {}
    for group in groups:
        cid = int(group["id"])
        widget = f"clu_thresh_{cid}"
        store = f"clu.thresh.{cid}"
        color = CLUSTER_PALETTE[cid % len(CLUSTER_PALETTE)]
        name = group.get("name", f"cluster {cid}")
        st.markdown(
            f"<span style='display:inline-block;width:12px;height:12px;"
            f"background:{color};border-radius:2px;margin-right:6px;"
            f"vertical-align:middle;'></span>"
            f"**{name}** (cluster {cid})",
            unsafe_allow_html=True,
        )
        v = st.slider(
            "threshold",
            0.0, 1.0,
            step=0.01,
            key=widget,
            label_visibility="collapsed",
        )
        st.session_state[store] = float(v)
        new_thresh[cid] = float(v)

        if assignments_df is not None and "max_posterior" in assignments_df.columns:
            sub = assignments_df[assignments_df["cluster_label"] == cid]
            n_pass = int((sub["max_posterior"] >= v).sum())
            n_total = int(len(sub))
            st.caption(f"{n_pass:,} / {n_total:,} pass")

    if st.button(
        "↺ Reset thresholds to 0.50",
        key="clu_reset_thresh",
        use_container_width=True,
    ):
        for group in groups:
            cid = int(group["id"])
            st.session_state[f"clu.thresh.{cid}"] = 0.50
            st.session_state.pop(f"clu_thresh_{cid}", None)
        st.rerun()

    return new_thresh


def _render_threshold_apply_button(
    analysis_folder: str,
    new_thresh: Dict[int, float],
) -> None:
    """Disk-persist button — separate from the live preview."""
    if not new_thresh:
        return
    if st.button(
        "💾 Save thresholds to disk",
        key="clu_apply_thresh",
        type="primary",
        use_container_width=True,
    ):
        try:
            summary = apply_thresholds(
                analysis_folder=analysis_folder,
                cluster_thresholds=new_thresh,
            )
            st.success(
                f"Thresholds saved: {summary['n_pass']:,} / "
                f"{summary['n_total']:,} pass."
            )
            st.rerun()
        except Exception as e:
            st.error(str(e))


def _render_cluster_sizes(labels: Dict[str, Any]) -> None:
    st.subheader("Cluster sizes")
    groups = labels.get("groups", [])
    if not groups:
        st.caption("No clusters to display.")
        return
    sizes_df = pd.DataFrame(
        [
            {"cluster": g.get("name", f"cluster {g.get('id', '?')}"),
             "size": int(g.get("size", 0))}
            for g in groups
        ]
    )
    st.bar_chart(sizes_df, x="cluster", y="size")


# ─── Top-level renderer ──────────────────────────────────────────────────

def _render_fit_gmm_button(
    analysis_folder: str, seed_groups: List[Dict[str, Any]]
) -> None:
    """Fit GMM button + ``fit_scope`` selector — sidebar drawer only.

    Fit scope choices:
      * **Seeds-only (recommended)** — GMM trains on the union of
        seed members so covariances stay tight around the user's
        seeds. Non-seed selector-passing domains far from any seed
        get low posteriors and are auto-rejected by the threshold.
        Resolves "시드 근처가 아닌데도 selection 됐으면 다 피팅이
        되고 있잖아".
      * **All selected** — legacy behaviour: EM runs over every
        selector-passing domain, with seeds providing only
        ``means_init``. Useful when seeds are sparse and you need
        EM to discover the broader distribution.
    """
    can_fit = len(seed_groups) >= 2
    if not can_fit:
        st.caption("Need ≥2 seed groups to fit.")

    # fit_scope canonical-store + widget (mode-button rerun safe).
    if "clu.fit_scope" not in st.session_state:
        st.session_state["clu.fit_scope"] = "seeds"
    if "clu_fit_scope" not in st.session_state:
        st.session_state["clu_fit_scope"] = st.session_state["clu.fit_scope"]
    scope_label = st.radio(
        "Fit on",
        options=["Seeds only (recommended)", "All selected (legacy)"],
        index=0 if st.session_state["clu_fit_scope"] == "seeds" else 1,
        key="clu_fit_scope_radio",
        horizontal=False,
    )
    fit_scope = "seeds" if scope_label.startswith("Seeds") else "all"
    st.session_state["clu.fit_scope"] = fit_scope
    st.session_state["clu_fit_scope"] = fit_scope

    if not st.button(
        "▶ Fit GMM",
        type="primary",
        disabled=not can_fit,
        key="clu_fit",
        use_container_width=True,
    ):
        return

    progress_bar = st.progress(0.0, "Starting...")
    status = st.empty()

    def cb(pct: float, msg: str) -> None:
        progress_bar.progress(pct, msg)
        status.caption(msg)

    try:
        result = run_clustering_step(
            analysis_folder=analysis_folder,
            seed_groups=seed_groups,
            fit_scope=fit_scope,
            progress_callback=cb,
        )
        progress_bar.progress(1.0, "Done")
        _render_diagnostics(result)
        st.success(
            f"GMM fitted on **{fit_scope}**: "
            f"{result.get('n_clusters', '?')} clusters · "
            f"assigned={result.get('n_assigned', '?')} · "
            f"unassigned={result.get('n_unassigned', '?')}"
        )
        st.rerun()
    except Exception as e:
        st.error(str(e))


def render_clustering_sidebar(
    state: _brushing.BrushingState,
    stats: Dict[str, Any],
    analysis_folder: str,
    labels: Optional[Dict[str, Any]],
    assignments_df: Optional[pd.DataFrame],
) -> tuple[str, str, bool, Dict[int, float]]:
    """Render the Clustering control drawer in the sidebar.

    Mirrors the Selector tab drawer (commit 1e74935 / v0.2.4): a single
    ``st.sidebar.expander("⚙ Clustering controls")`` owns the
    interaction-mode buttons, undo/redo/clear, seed-group authoring,
    axis pickers, optional 3D toggle, the Fit GMM button, AND (when a
    fit is on disk) the per-cluster threshold sliders.

    Returns ``(x_axis, y_axis, show_3d, live_thresholds)`` so the tab
    body can render the same scatter + use the live thresholds to
    re-color cluster members on the fly.
    """
    seed_groups = _ensure_session_seed_groups()
    live_thresh: Dict[int, float] = {}
    with st.sidebar.expander("⚙ Clustering controls", expanded=True):
        _brushing.render_mode_controls(state, "clustering", compact=True)
        st.divider()
        st.caption("**Seed groups**")
        # Compute the live thresholds first so the cluster-import
        # button reflects the slider state. We call this twice (once
        # here for the import button, again below where the slider
        # widgets live) — values come from the same canonical store
        # so they stay consistent.
        if labels is not None:
            live_thresh = {
                int(g["id"]): float(
                    st.session_state.get(
                        f"clu.thresh.{int(g['id'])}",
                        float((labels.get("thresholds") or {}).get(str(int(g["id"])), 0.5)),
                    )
                )
                for g in labels.get("groups", [])
            }
        _render_seed_group_panel(
            stats, state,
            analysis_folder=analysis_folder,
            labels=labels,
            assignments_df=assignments_df,
            live_thresholds=live_thresh,
        )
        st.divider()

        # Axis pickers + optional 3D toggle (mirror Selector layout).
        st.caption("2D scatter axes")
        x_axis, y_axis = _render_clu_axis_pickers()
        if "clu.show_3d" not in st.session_state:
            st.session_state["clu.show_3d"] = False
        if "clu_show_3d" not in st.session_state:
            st.session_state["clu_show_3d"] = bool(
                st.session_state["clu.show_3d"]
            )
        show_3d = st.checkbox(
            "Show 3D RGB",
            help="Render the 3D R-G-B context pane below the scatter "
                 "(display only — no lasso events).",
            key="clu_show_3d",
            on_change=_on_clu_show_3d_change,
        )
        st.divider()
        _render_fit_gmm_button(analysis_folder, seed_groups)

        # Per-cluster thresholds — only when a Fit is on disk. Live
        # preview reshapes the scatter immediately; the disk save is a
        # separate explicit button.
        if labels is not None:
            st.divider()
            st.caption("**Per-cluster thresholds (live)**")
            live_thresh = _render_per_cluster_thresholds_sidebar(
                labels, assignments_df,
            )
            _render_threshold_apply_button(analysis_folder, live_thresh)

    return x_axis, y_axis, show_3d, live_thresh


def render_tab_clustering(
    raw_images_dir: str,
    annotations_path: str,
    analysis_folder: str,
) -> None:
    """Render the Clustering tab.

    Layout (mirrors Selector for consistency):
        sidebar drawer  ⚙ — mode / seed groups / axes / 3D toggle / Fit
        body           — 2D scatter ‖ raw image preview
                         (optional 3D pane below)
                         seed group summary table
                         committed-fit thresholds + size chart
    """
    if not analysis_folder:
        st.warning("Set analysis_folder in sidebar to enable the Clustering tab.")
        return

    state = _brushing.get_brushing_state("clustering")

    st.info(
        "Clustering operates on the **selector-committed domain set** "
        "(tab 2 → Commit). Build seed groups in the sidebar drawer, "
        "then click ▶ Fit GMM."
    )

    # Prereq gate
    manifest = load_manifest(analysis_folder)
    stats_entry = manifest.steps.get("domain_stats")
    selector_entry = manifest.steps.get("selector")
    if stats_entry is None or stats_entry.completed_at is None:
        st.warning("⚠ Domain Stats not committed. Run Compute → Domain Stats first.")
        return
    if selector_entry is None or selector_entry.completed_at is None:
        st.warning("⚠ Selector not committed. Commit selection in tab 2 first.")
        return

    stats = _load_inputs(analysis_folder)
    if stats is None:
        st.warning("⚠ stats.npz or selection.parquet missing on disk.")
        return

    n_total = int(len(stats["flake_ids"]))
    n_sel = int(stats["sel_count"])
    pct_sel = (100.0 * n_sel / n_total) if n_total else 0.0
    st.success(
        f"✅ Working on selector-committed set: **{n_sel:,} / {n_total:,} "
        f"domains ({pct_sel:.1f}%)** "
        f"· last commit {selector_entry.completed_at}. "
        f"Re-commit in Selector if its filter / lasso changed since."
    )

    # Auto-hydrate seed groups from the last fit on first visit per
    # analysis_folder. The user's edits aren't clobbered: we only load
    # when the in-memory list is empty (idempotent flag inside).
    if _maybe_autoload_seed_groups(analysis_folder):
        # Note: ``st.toast(icon=...)`` only accepts a single emoji
        # character, not the "↻" arrow we use elsewhere — fall back
        # to a plain message so the toast still surfaces.
        st.toast(
            "Loaded seed groups from the last fit. Edit in the sidebar.",
            icon="🔄",
        )
        st.rerun()

    # Cluster assignment (if previously committed) drives the scatter colors.
    labels = _load_committed_clustering(analysis_folder)
    assignments_df = _load_committed_assignments(analysis_folder)

    # Sidebar drawer needs the labels/assignments to render the live
    # threshold sliders, so we load it first.
    x_axis, y_axis, show_3d, live_thresh = render_clustering_sidebar(
        state, stats, analysis_folder, labels, assignments_df,
    )

    cluster_assign: Optional[Dict[int, int]] = None
    if labels:
        cluster_assign = _live_cluster_assign(
            labels, assignments_df, live_thresh,
        )

    arrays = _build_clu_scatter_arrays(stats, state, x_axis, y_axis)
    seed_groups = _ensure_session_seed_groups()
    edit_group_ids = _edit_group_member_ids(seed_groups)
    edit_target = st.session_state.get("cluster_target_group")

    # Side-by-side: configurable 2D scatter on the left, raw image
    # preview on the right. Mirrors the Selector body layout.
    body_l, body_r = st.columns([1, 1])
    with body_l:
        if arrays is None:
            st.info(
                "Selector commit kept zero domains. Loosen filters or "
                "re-commit a non-empty Selected set in tab 2."
            )
        else:
            _render_clu_2d_scatter(
                arrays, state, cluster_assign, seed_groups, edit_group_ids,
                x_axis, y_axis, height=520,
            )
            cap_parts = [f"Selected (lasso): {len(state.selected_ids):,}"]
            if edit_target and edit_group_ids:
                cap_parts.append(
                    f"Editing group **{edit_target}** "
                    f"({len(edit_group_ids):,} domains, orange ring)"
                )
            st.caption(" · ".join(cap_parts))
    with body_r:
        focus = _focus_domain_id(state)
        render_image_preview(
            raw_images_dir=raw_images_dir,
            annotations_path=annotations_path,
            domain_id=focus,
            n_selected=len(state.selected_ids),
            height=520,
        )

    # Optional 3D pane below the side-by-side row.
    if show_3d and arrays is not None:
        st.divider()
        _render_clu_3d_rgb(
            arrays, state, cluster_assign, seed_groups, edit_group_ids,
            height=520,
        )

    st.divider()

    # Seed group summary table — full-width in the body where there's
    # room (the sidebar version was too cramped).
    if seed_groups:
        with st.expander(
            f"Seed groups ({len(seed_groups)})", expanded=True
        ):
            df = _seed_groups_to_table(
                seed_groups, stats["repr_rgbs"], stats["flake_ids"],
            )
            # Style: paint the ``color`` cell with its hex value as
            # background (swatch), bold the ``name`` of the edit
            # target so the user can match a scatter colour back to
            # the table row.
            def _style_color_swatch(val: str) -> str:
                return (
                    f"background-color: {val}; color: {val};"
                )

            def _style_edit_target(s: pd.Series) -> List[str]:
                if edit_target and s.get("name") == edit_target:
                    return ["font-weight: 700; background-color: #fff3cd;"] * len(s)
                return [""] * len(s)

            # ``Styler.applymap`` was deprecated in pandas 2.1+; use
            # the cell-wise ``map`` method.
            styler = df.style
            cell_map = getattr(styler, "map", None) or styler.applymap
            styled = (
                cell_map(_style_color_swatch, subset=["color"])
                .apply(_style_edit_target, axis=1)
            )
            st.dataframe(styled, width="stretch", height=240)

    # Threshold sliders moved to the sidebar drawer (live preview);
    # body keeps just the size chart + last-fit timestamp.
    if labels:
        clustering_entry = manifest.steps.get("clustering")
        if clustering_entry and clustering_entry.completed_at:
            st.caption(f"Last fit: {clustering_entry.completed_at}")
        st.divider()
        _render_cluster_sizes(labels)
