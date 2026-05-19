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


# ─── Data loading ────────────────────────────────────────────────────────

def _load_inputs(analysis_folder: str) -> Optional[Dict[str, Any]]:
    """Return dict with stats arrays + selected_mask, or None if prereq missing."""
    stats_path = Path(analysis_folder) / "02_domain_stats" / "stats.npz"
    sel_path = Path(analysis_folder) / "03_selector" / "selection.parquet"
    if not stats_path.exists() or not sel_path.exists():
        return None
    npz = np.load(stats_path, allow_pickle=False)
    sel_df = pd.read_parquet(sel_path)

    flake_ids = npz["flake_ids"].astype(np.int64)
    sel_set = set(sel_df.loc[sel_df["selected"].astype(bool), "domain_id"].astype(int).tolist())
    selected_mask = np.isin(flake_ids, list(sel_set))

    return {
        "flake_ids": flake_ids,
        "repr_rgbs": npz["repr_rgbs"],
        "selected_mask": selected_mask,
        "sel_count": int(selected_mask.sum()),
    }


def _load_committed_clustering(analysis_folder: str) -> Optional[Dict[str, Any]]:
    """Return labels.json contents if committed, else None."""
    p = Path(analysis_folder) / "04_clustering" / "labels.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


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
    """5-column summary: name, count, mean_r, mean_g, mean_b."""
    rows: List[Dict[str, Any]] = []
    for g in seed_groups:
        ids_g = list(g.get("domain_ids", []))
        if ids_g:
            mask = np.isin(ids, ids_g)
            idx = np.where(mask)[0]
        else:
            idx = np.array([], dtype=np.int64)
        if idx.size > 0:
            mean_rgb = rgb[idx].mean(axis=0)
            rows.append({
                "name": g.get("name", "?"),
                "count": int(len(ids_g)),
                "mean_r": round(float(mean_rgb[0]), 1),
                "mean_g": round(float(mean_rgb[1]), 1),
                "mean_b": round(float(mean_rgb[2]), 1),
            })
        else:
            rows.append({
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
) -> None:
    """Seed group management UI: Add / Remove / Rename / Clear All."""
    seed_groups = _ensure_session_seed_groups()
    st.subheader("Seed groups")

    if seed_groups:
        df = _seed_groups_to_table(
            seed_groups, stats["repr_rgbs"], stats["flake_ids"]
        )
        st.dataframe(df, width="stretch", height=200)
    else:
        st.caption("No seed groups yet. Brush domains in the scatter, then click + Add.")

    selected_ids = state.selected_ids
    st.caption(f"Brush buffer: {len(selected_ids)} domain(s) ready to add (mode={state.mode}).")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        new_name = st.text_input(
            "New group name",
            key="cluster_new_name",
            placeholder="e.g. graphite",
        )
        if st.button("+ Add", key="cluster_add_group"):
            if not new_name:
                st.warning("Enter a group name first.")
            elif not selected_ids:
                st.warning("Brush some domains in the scatter first.")
            elif any(g.get("name") == new_name for g in seed_groups):
                st.warning(f"Group '{new_name}' already exists.")
            else:
                seed_groups.append({
                    "name": new_name,
                    "domain_ids": sorted(int(i) for i in selected_ids),
                })
                _brushing.clear_selection(state)
                st.rerun()

    names = [g["name"] for g in seed_groups]
    with col2:
        if names:
            target = st.selectbox(
                "Group", names, key="cluster_target_group"
            )
            if st.button("− Remove", key="cluster_remove_group"):
                seed_groups[:] = [g for g in seed_groups if g["name"] != target]
                st.rerun()
        else:
            st.caption("(no groups to remove)")

    with col3:
        if names:
            target_r = st.selectbox(
                "Rename target", names, key="cluster_rename_target"
            )
            new_n = st.text_input(
                "New name", key="cluster_rename_new"
            )
            if st.button("✏ Rename", key="cluster_rename_btn"):
                if not new_n:
                    st.warning("Enter a new name.")
                else:
                    for g in seed_groups:
                        if g["name"] == target_r:
                            g["name"] = new_n
                            break
                    st.rerun()
        else:
            st.caption("(no groups to rename)")

    with col4:
        if st.button("↺ Clear All", key="cluster_clear_all"):
            seed_groups.clear()
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


def _render_4pane_scatter(
    stats: Dict[str, Any],
    cluster_assign: Optional[Dict[int, int]],
    state: _brushing.BrushingState,
) -> None:
    """4-pane scatter (3D + R-G + R-B + G-B) with linked brushing.

    The 2D panes' ``dragmode`` follows ``state.interaction_mode``:
    ``pan`` for single-pick, ``lasso`` for lasso mode.
    """
    rgb_all = stats["repr_rgbs"]
    flake_ids = stats["flake_ids"].astype(np.int64)
    sel_mask = stats["selected_mask"]

    rgb_sel = rgb_all[sel_mask]
    ids_sel = flake_ids[sel_mask]
    n = len(ids_sel)
    if n == 0:
        st.warning("Selector kept zero domains; cannot render scatter.")
        return

    sub_idx = _downsample_indices(
        n,
        flake_ids=ids_sel,
        must_include_ids=state.selected_ids,
    )
    rgb_sub = rgb_sel[sub_idx]
    ids_sub = ids_sel[sub_idx]
    if n > _MAX_POINTS:
        st.caption(
            f"Showing {_MAX_POINTS:,} of {n:,} domains "
            f"(seeded random downsample for plot perf)."
        )

    if cluster_assign:
        base_colors = np.array([
            CLUSTER_PALETTE[cluster_assign[int(fid)] % len(CLUSTER_PALETTE)]
            if int(fid) in cluster_assign and cluster_assign[int(fid)] >= 0
            else NEUTRAL_GRAY
            for fid in ids_sub
        ])
    else:
        base_colors = np.full(len(ids_sub), NEUTRAL_GRAY)

    selected = state.selected_ids
    dragmode = _brushing.get_dragmode(state)
    interaction = state.interaction_mode
    pane_hint = (
        "click to select · drag to pan · scroll to zoom"
        if interaction == _brushing.INTERACTION_SINGLE
        else "lasso/box to brush · scroll to zoom"
    )

    # Embed interaction mode in the chart key so Streamlit treats the
    # chart as a different element when dragmode flips (Task 1 fix —
    # mirrors the Selector tab logic).
    suffix = interaction

    col1, col2 = st.columns(2)
    with col1:
        st.caption("3D R-G-B (display only)")
        fig3d = _brushing.make_3d_scatter(
            rgb_sub, ids_sub,
            base_colors=base_colors, selected_ids=selected,
        )
        _brushing.render_scatter(fig3d, key="clu_pane_3d", on_select=False)

    with col2:
        st.caption(f"R vs G ({pane_hint})")
        fig_rg = _brushing.make_2d_scatter(
            rgb_sub[:, 0], rgb_sub[:, 1], ids_sub,
            base_colors=base_colors, selected_ids=selected,
            x_label="R", y_label="G",
            dragmode=dragmode,
        )
        evt_rg = _brushing.render_scatter(
            fig_rg, key=f"clu_pane_rg_{suffix}", interaction_mode=interaction,
        )
        if _dispatch_event(evt_rg, state):
            st.rerun()

    col3, col4 = st.columns(2)
    with col3:
        st.caption(f"R vs B ({pane_hint})")
        fig_rb = _brushing.make_2d_scatter(
            rgb_sub[:, 0], rgb_sub[:, 2], ids_sub,
            base_colors=base_colors, selected_ids=selected,
            x_label="R", y_label="B",
            dragmode=dragmode,
        )
        evt_rb = _brushing.render_scatter(
            fig_rb, key=f"clu_pane_rb_{suffix}", interaction_mode=interaction,
        )
        if _dispatch_event(evt_rb, state):
            st.rerun()

    with col4:
        st.caption(f"G vs B ({pane_hint})")
        fig_gb = _brushing.make_2d_scatter(
            rgb_sub[:, 1], rgb_sub[:, 2], ids_sub,
            base_colors=base_colors, selected_ids=selected,
            x_label="G", y_label="B",
            dragmode=dragmode,
        )
        evt_gb = _brushing.render_scatter(
            fig_gb, key=f"clu_pane_gb_{suffix}", interaction_mode=interaction,
        )
        if _dispatch_event(evt_gb, state):
            st.rerun()


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


def _render_per_cluster_thresholds(
    analysis_folder: str, labels: Dict[str, Any]
) -> None:
    """Per-cluster posterior threshold sliders + Apply / Reset buttons."""
    st.subheader("Per-cluster probability thresholds")
    n_clusters = int(labels.get("n_clusters", 0))
    if n_clusters == 0:
        st.caption("No clusters to threshold.")
        return

    current_thresh = labels.get("thresholds", {})
    new_thresh: Dict[int, float] = {}

    groups = labels.get("groups", [])
    cols = st.columns(min(n_clusters, 5))
    for i, group in enumerate(groups):
        with cols[i % len(cols)]:
            cid = int(group["id"])
            default = float(current_thresh.get(str(cid), 0.50))
            v = st.slider(
                f"{group.get('name', f'cluster {cid}')} (cluster {cid})",
                0.0, 1.0, default, 0.01,
                key=f"clu_thresh_{cid}",
            )
            new_thresh[cid] = float(v)

    col1, col2, _ = st.columns([1, 1, 3])
    with col1:
        if st.button("▶ Apply thresholds", key="clu_apply_thresh", type="primary"):
            try:
                summary = apply_thresholds(
                    analysis_folder=analysis_folder,
                    cluster_thresholds=new_thresh,
                )
                st.success(
                    f"Thresholds applied: {summary['n_pass']:,} / "
                    f"{summary['n_total']:,} pass"
                )
                st.rerun()
            except Exception as e:
                st.error(str(e))
    with col2:
        if st.button("↺ Reset all to 0.50", key="clu_reset_thresh"):
            for cid in range(n_clusters):
                st.session_state.pop(f"clu_thresh_{cid}", None)
            st.rerun()


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

def render_tab_clustering(
    raw_images_dir: str,
    annotations_path: str,
    analysis_folder: str,
) -> None:
    """Render the Clustering tab."""
    if not analysis_folder:
        st.warning("Set analysis_folder in sidebar to enable the Clustering tab.")
        return

    state = _brushing.get_brushing_state("clustering")
    _brushing.render_keyboard_shortcuts()
    _brushing.render_wheel_capture()

    info_col, help_col = st.columns([6, 1])
    with info_col:
        st.info(
            "Clustering operates on the selector-narrowed domain set. "
            "Default mode is Single-pick — click a point to identify it. "
            "Press L for Lasso brushing to build seed groups; sub-modes R/A/D "
            "combine selections (Replace / Add / Subtract). Click + Add attaches "
            "the brush buffer to a seed group."
        )
    with help_col:
        _brushing.render_help_button(key="clustering_help_btn")

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

    st.success(
        f"✅ Selector ready · {stats['sel_count']:,} domains "
        f"(last commit: {selector_entry.completed_at})"
    )

    # Mode controls + Undo/Redo/Clear
    _brushing.render_mode_controls(state, "clustering")

    # Seed group authoring
    _render_seed_group_panel(stats, state)

    st.divider()

    # Cluster assignment (if previously committed) drives the scatter colors.
    labels = _load_committed_clustering(analysis_folder)
    cluster_assign: Optional[Dict[int, int]] = None
    if labels:
        cluster_assign = {
            int(k): int(v) for k, v in labels.get("assignments", {}).items()
        }

    # 4-pane scatter
    _render_4pane_scatter(stats, cluster_assign, state)

    st.divider()

    # Fit GMM
    seed_groups = _ensure_session_seed_groups()
    can_fit = len(seed_groups) >= 2
    if not can_fit:
        st.caption("Need at least 2 seed groups before fitting.")

    if st.button(
        "▶ Fit GMM",
        type="primary",
        disabled=not can_fit,
        key="clu_fit",
    ):
        progress_bar = st.progress(0.0, "Starting...")
        status = st.empty()

        def cb(pct: float, msg: str) -> None:
            progress_bar.progress(pct, msg)
            status.caption(msg)

        try:
            result = run_clustering_step(
                analysis_folder=analysis_folder,
                seed_groups=seed_groups,
                progress_callback=cb,
            )
            progress_bar.progress(1.0, "Done")
            _render_diagnostics(result)
            st.success(
                f"GMM fitted: {result.get('n_clusters', '?')} clusters · "
                f"assigned={result.get('n_assigned', '?')} · "
                f"unassigned={result.get('n_unassigned', '?')}"
            )
            st.rerun()
        except Exception as e:
            st.error(str(e))

    # If a fit is on disk, expose thresholds + size chart.
    if labels:
        clustering_entry = manifest.steps.get("clustering")
        if clustering_entry and clustering_entry.completed_at:
            st.caption(f"Last fit: {clustering_entry.completed_at}")
        st.divider()
        _render_per_cluster_thresholds(analysis_folder, labels)
        st.divider()
        _render_cluster_sizes(labels)
