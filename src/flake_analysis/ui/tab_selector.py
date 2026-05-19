"""Selector tab — 4-pane RGB scatter + 5-metric bidirectional filter + flake list.

Per-domain image preview, mode toggles (Replace/Add/Subtract), undo/redo,
and scroll-zoom were added in v0.1.2 via the shared ``_brushing`` helper.

The 5-metric filter is the actual selection contract; the lasso/box
brushing is for cross-pane inspection only and does not modify metric
ranges.

Mockup reference: ``04_tab_selector.html``.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, Optional, Set

import numpy as np
import pandas as pd
import streamlit as st

from flake_analysis.pipeline.selector import run_selector_step
from flake_analysis.state.manifest import load_manifest
from flake_analysis.ui import _brushing
from flake_analysis.ui._image_preview import render_image_preview


# ─── Data loading + filtering ────────────────────────────────────────────

def _load_stats_npz(analysis_folder: str) -> Optional[Dict[str, np.ndarray]]:
    path = Path(analysis_folder) / "02_domain_stats" / "stats.npz"
    if not path.exists():
        return None
    npz = np.load(path, allow_pickle=False)
    return {key: npz[key] for key in npz.files}


def _apply_filter(
    stats: Dict[str, np.ndarray], params: Dict[str, Optional[float]]
) -> np.ndarray:
    """Return boolean mask of accepted domains based on 5-metric bounds."""
    n = len(stats["flake_ids"])
    mask = np.ones(n, dtype=bool)

    areas = stats.get("areas")
    std_pcts = stats.get("std_pcts")
    sam2 = stats.get("sam2")

    if areas is not None:
        if params.get("area_min") is not None:
            mask &= areas >= params["area_min"]
        if params.get("area_max") is not None:
            mask &= areas <= params["area_max"]

    if std_pcts is not None:
        for i, ch in enumerate(("std_r", "std_g", "std_b")):
            mn = params.get(f"{ch}_min")
            mx = params.get(f"{ch}_max")
            if mn is not None:
                mask &= std_pcts[:, i] >= mn
            if mx is not None:
                mask &= std_pcts[:, i] <= mx

    if sam2 is not None:
        if params.get("sam2_min") is not None:
            mask &= sam2 >= params["sam2_min"]
        if params.get("sam2_max") is not None:
            mask &= sam2 <= params["sam2_max"]
    # If sam2 missing, sam2 bounds are ignored (allow_missing semantics).

    return mask


# ─── Filter controls ─────────────────────────────────────────────────────

# (key, label, lo, hi, default_min, default_max, step, fmt)
_METRIC_DEFS = (
    ("area",  "Area (px)",   0.0, 1_000_000.0, 0.0, 1_000_000.0, 10.0,  "%.0f"),
    ("std_r", "Std R %",     0.0,       100.0, 0.0,       100.0,  0.5,  "%.2f"),
    ("std_g", "Std G %",     0.0,       100.0, 0.0,       100.0,  0.5,  "%.2f"),
    ("std_b", "Std B %",     0.0,       100.0, 0.0,       100.0,  0.5,  "%.2f"),
    ("sam2",  "SAM2 score",  0.0,         1.0, 0.0,         1.0, 0.05,  "%.2f"),
)


def _render_filter_controls() -> Dict[str, Optional[float]]:
    """Render 5-metric × min/max sliders. Returns params dict."""
    st.subheader("5-metric filter")
    cols = st.columns(5)
    params: Dict[str, Optional[float]] = {}

    for i, (key, label, lo, hi, mn_default, mx_default, step, fmt) in enumerate(_METRIC_DEFS):
        with cols[i]:
            st.caption(label)
            mn = st.number_input(
                f"{key} min",
                min_value=float(lo),
                max_value=float(hi),
                value=float(mn_default),
                step=float(step),
                format=fmt,
                key=f"sel_{key}_min",
            )
            mx = st.number_input(
                f"{key} max",
                min_value=float(lo),
                max_value=float(hi),
                value=float(mx_default),
                step=float(step),
                format=fmt,
                key=f"sel_{key}_max",
            )
            params[f"{key}_min"] = mn if mn != mn_default else None
            params[f"{key}_max"] = mx if mx != mx_default else None

    return params


def _clear_filter_session_keys() -> None:
    """Reset all selector filter widgets to defaults by deleting keys."""
    for key, *_ in _METRIC_DEFS:
        st.session_state.pop(f"sel_{key}_min", None)
        st.session_state.pop(f"sel_{key}_max", None)


# ─── 4-pane scatter (linked brushing via shared _brushing helper) ────────

# Static downsample cap for PR 2.3. Future: zoom-aware (M3).
_MAX_POINTS = 5000


def _downsample_indices(
    n: int,
    *,
    flake_ids: Optional[np.ndarray] = None,
    must_include_ids: Optional[Set[int]] = None,
    cap: int = _MAX_POINTS,
) -> np.ndarray:
    """Pick up to ``cap`` indices, but always keep ``must_include_ids``.

    The point of this is so that lasso/click-selected domains stay visible
    on the scatter even when the dataset is downsampled. If everything
    fits, we just return ``np.arange(n)``; otherwise we union a seeded
    random subset with the indices of the must-include set, then trim.
    """
    if n <= cap:
        return np.arange(n)
    rng = np.random.default_rng(0)
    base = rng.choice(n, cap, replace=False)
    if must_include_ids and flake_ids is not None and len(must_include_ids) > 0:
        keep_mask = np.isin(flake_ids, list(must_include_ids))
        keep_idx = np.where(keep_mask)[0]
        if keep_idx.size:
            # Union; if total > cap, drop random non-keep entries to fit.
            combined = np.unique(np.concatenate([base, keep_idx]))
            if combined.size > cap:
                # Keep all must-include + fill the rest from base random.
                non_keep = np.setdiff1d(base, keep_idx, assume_unique=False)
                space = max(cap - keep_idx.size, 0)
                base = np.concatenate([keep_idx, non_keep[:space]])
                return np.sort(base)
            return np.sort(combined)
    return np.sort(base)


def _dispatch_event(event, state: _brushing.BrushingState) -> bool:
    """Route a Plotly chart event based on the active interaction mode.

    Single-pick → ``handle_click_event`` (replace selection w/ 1 id, clear
    focus_id since the user explicitly clicked the scatter).
    Lasso → ``handle_selection_event`` (mode-aware combine).

    Returns True iff state was modified.
    """
    if state.interaction_mode == _brushing.INTERACTION_SINGLE:
        if _brushing.handle_click_event(event, state):
            # An explicit scatter click overrides any prior row focus.
            state.focus_id = None
            return True
        return False
    return _brushing.handle_selection_event(event, state)


def _render_4pane_scatter(
    stats: Dict[str, np.ndarray],
    accept_mask: np.ndarray,
    state: _brushing.BrushingState,
) -> None:
    """4-pane RGB scatter (3D + R-G + R-B + G-B) with linked brushing.

    Color encoding: green=accepted, red=rejected.
    Lasso/box-selected points get an orange ring across all panes.

    The 2D panes' ``dragmode`` follows ``state.interaction_mode``:
    ``pan`` for single-pick (left-drag pans, click selects 1 pt) and
    ``lasso`` for lasso mode.
    """
    rgb = stats["repr_rgbs"]
    flake_ids = stats["flake_ids"].astype(np.int64)
    n = len(flake_ids)

    sub_idx = _downsample_indices(
        n,
        flake_ids=flake_ids,
        must_include_ids=state.selected_ids,
    )
    rgb_sub = rgb[sub_idx]
    ids_sub = flake_ids[sub_idx]
    accepted_sub = accept_mask[sub_idx]

    base_colors = np.where(accepted_sub, "#43a047", "#e53935")

    if n > _MAX_POINTS:
        st.caption(
            f"Showing {_MAX_POINTS:,} of {n:,} domains "
            f"(seeded random downsample for plot perf)."
        )

    selected = state.selected_ids
    dragmode = _brushing.get_dragmode(state)
    interaction = state.interaction_mode
    pane_hint = (
        "click to select · drag to pan · scroll to zoom"
        if interaction == _brushing.INTERACTION_SINGLE
        else "lasso/box to brush · scroll to zoom"
    )

    # Embed interaction mode in the chart key so Streamlit treats the
    # chart as a different element when dragmode flips. Without this
    # suffix, the cached event payload from a prior lasso could replay
    # against the freshly-rebuilt 'pan' figure (Task 1 fix).
    suffix = interaction

    col1, col2 = st.columns(2)
    with col1:
        st.caption("3D R-G-B scatter (display only)")
        fig3d = _brushing.make_3d_scatter(
            rgb_sub, ids_sub,
            base_colors=base_colors, selected_ids=selected,
        )
        _brushing.render_scatter(fig3d, key="sel_pane_3d", on_select=False)

    with col2:
        st.caption(f"R vs G ({pane_hint})")
        fig_rg = _brushing.make_2d_scatter(
            rgb_sub[:, 0], rgb_sub[:, 1], ids_sub,
            base_colors=base_colors, selected_ids=selected,
            x_label="R", y_label="G",
            dragmode=dragmode,
        )
        evt_rg = _brushing.render_scatter(
            fig_rg, key=f"sel_pane_rg_{suffix}", interaction_mode=interaction,
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
            fig_rb, key=f"sel_pane_rb_{suffix}", interaction_mode=interaction,
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
            fig_gb, key=f"sel_pane_gb_{suffix}", interaction_mode=interaction,
        )
        if _dispatch_event(evt_gb, state):
            st.rerun()


# ─── Right pane: flake list ──────────────────────────────────────────────

def _render_flake_list(
    stats: Dict[str, np.ndarray],
    accept_mask: np.ndarray,
    state: _brushing.BrushingState,
) -> None:
    """Render the flake table with single-row click → focus_id binding.

    Row click sets ``state.focus_id`` to the clicked ``domain_id``. This
    drives the image preview without modifying the brushing
    ``selected_ids`` set — focus and brush selection are intentionally
    separate concepts (one identifies a domain to inspect, the other is
    the cross-pane brushing selection used by Selector / Clustering
    workflows).
    """
    flake_ids = stats["flake_ids"].astype(np.int64)
    rgb = stats["repr_rgbs"]
    std = stats["std_pcts"]
    areas = stats["areas"]
    sam2 = stats.get("sam2")
    if sam2 is None:
        sam2 = np.full(len(flake_ids), np.nan)

    selected_ids = state.selected_ids
    df = pd.DataFrame(
        {
            "domain_id": flake_ids,
            "area_px": areas,
            "mean_r": rgb[:, 0],
            "mean_g": rgb[:, 1],
            "mean_b": rgb[:, 2],
            "std_r%": std[:, 0],
            "std_g%": std[:, 1],
            "std_b%": std[:, 2],
            "sam2": sam2,
            "status": np.where(accept_mask, "accepted", "rejected"),
        }
    )
    if selected_ids:
        df.loc[df["domain_id"].isin(selected_ids), "status"] = "selected"

    # Newer Streamlit (>= 1.35) supports on_select="rerun" with
    # selection_mode="single-row" on st.dataframe. Older versions raise;
    # gracefully fall back to a non-interactive table so the tab still
    # renders.
    try:
        event = st.dataframe(
            df,
            height=300,
            use_container_width=True,
            on_select="rerun",
            selection_mode="single-row",
            key="selector_flake_list",
        )
    except (TypeError, ValueError):
        st.dataframe(df, height=300, use_container_width=True)
        return

    selection = (
        event.get("selection") if isinstance(event, dict) else getattr(event, "selection", None)
    )
    if selection is None:
        return
    rows = (
        selection.get("rows")
        if isinstance(selection, dict)
        else getattr(selection, "rows", None)
    )
    if not rows:
        return
    try:
        row_idx = int(rows[0])
    except (TypeError, ValueError):
        return
    if 0 <= row_idx < len(df):
        new_focus = int(df.iloc[row_idx]["domain_id"])
        if state.focus_id != new_focus:
            state.focus_id = new_focus
            st.rerun()


# ─── Top-level renderer ──────────────────────────────────────────────────

def _focus_domain_id(state: _brushing.BrushingState) -> Optional[int]:
    """Pick the focused domain for the image preview panel.

    Priority order:

    1. ``state.focus_id`` — explicit row click in the flake list.
    2. ``min(selected_ids)`` — fallback when only the brushing set is set.
    3. ``None`` — neither focus nor selection.
    """
    if state.focus_id is not None:
        return int(state.focus_id)
    if not state.selected_ids:
        return None
    return min(state.selected_ids)


def render_tab_selector(
    raw_images_dir: str,
    annotations_path: str,
    analysis_folder: str,
) -> None:
    """Render the Selector tab."""
    if not analysis_folder:
        st.warning("Set analysis_folder in sidebar to enable the Selector tab.")
        return

    state = _brushing.get_brushing_state("selector")

    # Inject keyboard shortcut JS + wheel capture (best-effort — Streamlit
    # iframe sandbox may block them; the visible buttons remain primary).
    _brushing.render_keyboard_shortcuts()
    _brushing.render_wheel_capture()

    info_col, help_col = st.columns([6, 1])
    with info_col:
        st.info(
            "Default mode is Single-pick: left-click a point to focus one domain. "
            "Press L (or click Lasso: Replace) for lasso brushing across panes — "
            "use sub-modes Replace/Add/Subtract (R/A/D) to combine selections. "
            "Click a row in the flake list below to drive the image preview."
        )
    with help_col:
        _brushing.render_help_button(key="selector_help_btn")

    stats = _load_stats_npz(analysis_folder)
    if stats is None:
        st.warning("⚠ Domain Stats not computed. Run Compute → Domain Stats first.")
        return

    # Manifest gate (matches the pipeline wrapper precondition)
    manifest = load_manifest(analysis_folder)
    stats_entry = manifest.steps.get("domain_stats")
    if stats_entry is None or stats_entry.completed_at is None:
        st.warning(
            "⚠ stats.npz exists but Domain Stats is not recorded in manifest. "
            "Re-run Compute → Domain Stats."
        )
        return

    st.success(f"✅ Domain Stats ready (last run: {stats_entry.completed_at})")

    # Mode controls + Undo / Redo / Clear (selection history)
    _brushing.render_mode_controls(state, "selector")

    # Filter preset buttons
    col_a, col_b, _spacer = st.columns([1, 1, 5])
    with col_a:
        if st.button("✓ Select All", help="Clear all bounds (accept everything)"):
            _clear_filter_session_keys()
            st.rerun()
    with col_b:
        if st.button("↺ Reset filter", help="Reset filter widgets to defaults"):
            _clear_filter_session_keys()
            st.rerun()

    # 5-metric filter widgets
    params = _render_filter_controls()

    # Live accept/reject preview
    accept_mask = _apply_filter(stats, params)
    n_total = int(len(accept_mask))
    n_accepted = int(accept_mask.sum())
    pct = (100.0 * n_accepted / n_total) if n_total else 0.0
    selected_ids = state.selected_ids

    metric_cols = st.columns(3)
    with metric_cols[0]:
        st.metric("Accepted", f"{n_accepted:,} / {n_total:,}", f"{pct:.1f}%")
    with metric_cols[1]:
        st.metric("Rejected", f"{n_total - n_accepted:,}")
    with metric_cols[2]:
        st.metric("Brush selected", f"{len(selected_ids):,}")

    st.divider()

    # 4-pane scatter (linked brushing)
    _render_4pane_scatter(stats, accept_mask, state)

    st.divider()

    # Right-pane: flake list + raw image preview side-by-side.
    list_col, img_col = st.columns([3, 2])
    with list_col:
        st.subheader("Flake list")
        _render_flake_list(stats, accept_mask, state)
    with img_col:
        focus = _focus_domain_id(state)
        render_image_preview(
            raw_images_dir=raw_images_dir,
            annotations_path=annotations_path,
            domain_id=focus,
            n_selected=len(selected_ids),
        )

    st.divider()

    # Commit
    selector_entry = manifest.steps.get("selector")
    if selector_entry and selector_entry.completed_at:
        st.caption(f"Last commit: {selector_entry.completed_at}")

    if st.button(
        "✅ Commit selection",
        type="primary",
        help="Write 03_selector/selection.parquet and update manifest",
    ):
        progress_bar = st.progress(0.0, "Starting...")
        status = st.empty()

        def cb(pct: float, msg: str) -> None:
            progress_bar.progress(pct, msg)
            status.caption(msg)

        try:
            result = run_selector_step(
                analysis_folder=analysis_folder,
                progress_callback=cb,
                **params,
            )
            progress_bar.progress(1.0, "Done")
            st.success(
                f"Selection committed: {result['selected_count']:,} / "
                f"{result['total_count']:,} domains -> {result['output_path']}"
            )
            st.rerun()
        except Exception as e:
            st.error(str(e))
