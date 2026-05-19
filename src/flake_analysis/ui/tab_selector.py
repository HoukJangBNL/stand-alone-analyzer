"""Selector tab — single 2D scatter (axis-pickable) + filter + flake list.

The 5-metric filter is the actual selection contract; the lasso/box
brushing is for cross-pane inspection only and does not modify metric
ranges.

v0.2.1 layout overhaul:

* The historic 4-pane (3D RGB + R-G + R-B + G-B) was replaced by a
  single 2D scatter with X / Y axis dropdowns. The 3D pane never
  supported lasso/click selection so removing it is not a functional
  regression and the user can pick any pair of axes (R/G/B, std_*,
  area, sam2) from one chart.
* The raw image preview was moved out of the right column and now sits
  full-width directly below the scatter at a larger height (600 px) so
  morphology inspection is the focal point.
* The flake list moved to the bottom and grew Export buttons for the
  filtered list and the brush-selected subset (CSV download).

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
    """Render 5-metric × min/max sliders. Returns params dict.

    Pre-seeds session_state with defaults exactly once per session so the
    sliders bind purely by ``key=``. Passing ``value=`` AND ``key=`` made
    Streamlit reset the widget back to the default on every rerun (e.g.
    when the user clicks a mode button), wiping any filter ranges the
    user had typed in. Pre-seeding keeps user edits sticky across reruns.
    """
    st.subheader("5-metric filter")

    # We persist filter values in NON-widget keys (``filter.<metric>_min``)
    # because Streamlit garbage-collects widget keys (``sel_<metric>_min``)
    # whenever the script run that owned them does not re-instantiate the
    # widget. Mode-button clicks trigger a rerun whose ScriptRunner pass
    # apparently treats the about-to-be-rebuilt sliders as "not yet seen",
    # so it discards the prior widget state entry — the user's typed value
    # vanishes. Mirroring the value into a plain ``filter.*`` key sidesteps
    # that GC: we read from ``filter.*`` to populate the widget's value=
    # argument, and on every render we copy the widget output back into
    # ``filter.*`` so subsequent reruns can rehydrate.

    cols = st.columns(5)
    params: Dict[str, Optional[float]] = {}

    for i, (key, label, lo, hi, mn_default, mx_default, step, fmt) in enumerate(_METRIC_DEFS):
        store_min = f"filter.{key}_min"
        store_max = f"filter.{key}_max"
        if store_min not in st.session_state:
            st.session_state[store_min] = float(mn_default)
        if store_max not in st.session_state:
            st.session_state[store_max] = float(mx_default)

        with cols[i]:
            st.caption(label)
            mn = st.number_input(
                f"{key} min",
                min_value=float(lo),
                max_value=float(hi),
                value=float(st.session_state[store_min]),
                step=float(step),
                format=fmt,
                key=f"sel_{key}_min",
            )
            mx = st.number_input(
                f"{key} max",
                min_value=float(lo),
                max_value=float(hi),
                value=float(st.session_state[store_max]),
                step=float(step),
                format=fmt,
                key=f"sel_{key}_max",
            )
            # Persist back so the next rerun rehydrates from filter.*
            st.session_state[store_min] = float(mn)
            st.session_state[store_max] = float(mx)

            params[f"{key}_min"] = mn if mn != mn_default else None
            params[f"{key}_max"] = mx if mx != mx_default else None

    return params


def _clear_filter_session_keys() -> None:
    """Reset all selector filter widgets to defaults."""
    for key, _label, _lo, _hi, mn_default, mx_default, _step, _fmt in _METRIC_DEFS:
        st.session_state[f"filter.{key}_min"] = float(mn_default)
        st.session_state[f"filter.{key}_max"] = float(mx_default)
        st.session_state.pop(f"sel_{key}_min", None)
        st.session_state.pop(f"sel_{key}_max", None)


# ─── Axis pickers ───────────────────────────────────────────────────────

# Order matters — controls the dropdown layout and default index picks.
AVAILABLE_AXES = ("R", "G", "B", "area", "std_r", "std_g", "std_b", "sam2")


def _values_for_axis(stats: Dict[str, np.ndarray], axis: str) -> np.ndarray:
    """Return the per-domain numeric array backing an axis dropdown choice.

    Mapping:
        R/G/B          → ``repr_rgbs[:, 0/1/2]``
        std_r/g/b      → ``std_pcts[:, 0/1/2]``
        area           → ``areas``
        sam2           → ``sam2`` (zeros fallback when the column is missing)
    """
    rgb = stats["repr_rgbs"]
    std = stats["std_pcts"]
    if axis == "R":
        return rgb[:, 0]
    if axis == "G":
        return rgb[:, 1]
    if axis == "B":
        return rgb[:, 2]
    if axis == "std_r":
        return std[:, 0]
    if axis == "std_g":
        return std[:, 1]
    if axis == "std_b":
        return std[:, 2]
    if axis == "area":
        return stats["areas"]
    if axis == "sam2":
        sam = stats.get("sam2")
        if sam is None:
            return np.zeros(len(stats["flake_ids"]))
        return sam
    raise ValueError(f"unknown axis: {axis}")


# ─── Single 2D scatter (linked brushing via shared _brushing helper) ─────

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
    if state.interaction_mode == _brushing.INTERACTION_ZOOM:
        # Zoom mode emits no selection events; Plotly handles the
        # viewport change internally via dragmode="zoom".
        return False
    return _brushing.handle_selection_event(event, state)


def _render_axis_pickers() -> tuple[str, str]:
    """Render X / Y axis dropdowns. Returns the picked axis names.

    Defaults: X=R, Y=G (matches the most informative pane from the legacy
    4-pane layout). The widgets occupy the leftmost two columns of a
    [1, 1, 6] grid so they don't dominate the row.
    """
    pick_cols = st.columns([1, 1, 6])
    with pick_cols[0]:
        x_axis = st.selectbox(
            "X-axis",
            AVAILABLE_AXES,
            index=0,
            key="selector_x_axis",
        )
    with pick_cols[1]:
        y_axis = st.selectbox(
            "Y-axis",
            AVAILABLE_AXES,
            index=1,
            key="selector_y_axis",
        )
    return x_axis, y_axis


def _render_2d_scatter(
    stats: Dict[str, np.ndarray],
    accept_mask: np.ndarray,
    state: _brushing.BrushingState,
    x_axis: str,
    y_axis: str,
) -> None:
    """Single 2D scatter with linked brushing on (x_axis, y_axis).

    Color encoding: green=accepted, red=rejected.
    Lasso/box-selected points get an orange ring overlay.

    The chart's ``dragmode`` follows ``state.interaction_mode``:
    ``pan`` for single-pick (left-drag pans, click selects 1 pt) and
    ``lasso`` for lasso mode.
    """
    flake_ids_all = stats["flake_ids"].astype(np.int64)
    n_total = len(flake_ids_all)

    # Restrict the scatter to ACCEPTED domains only. The user filters with
    # the 5-metric sliders precisely to focus inspection on the accepted
    # subset; rejected domains were cluttering the view.
    # Selected rejected domains are still shown (must_include_ids) so the
    # user can see what they brushed even after tightening filters.
    visible_mask = accept_mask.copy()
    if state.selected_ids:
        sel_arr = np.fromiter(state.selected_ids, dtype=np.int64)
        visible_mask = visible_mask | np.isin(flake_ids_all, sel_arr)

    visible_idx = np.where(visible_mask)[0]
    n_visible = len(visible_idx)
    if n_visible == 0:
        st.info(
            "No domains pass the current filter. "
            "Loosen the metric ranges (or click ✓ Select All) to see anything."
        )
        return

    flake_ids = flake_ids_all[visible_idx]
    x_full = _values_for_axis(stats, x_axis)[visible_idx]
    y_full = _values_for_axis(stats, y_axis)[visible_idx]
    accepted_visible = accept_mask[visible_idx]

    sub_idx = _downsample_indices(
        n_visible,
        flake_ids=flake_ids,
        must_include_ids=state.selected_ids,
    )
    x_sub = x_full[sub_idx]
    y_sub = y_full[sub_idx]
    ids_sub = flake_ids[sub_idx]
    accepted_sub = accepted_visible[sub_idx]

    # Accepted = green; the only non-accepted dots that remain are
    # selected-but-now-rejected ones — render them in faded amber so
    # they're visibly distinct from clean accepted points.
    base_colors = np.where(accepted_sub, "#43a047", "#fbc02d")

    if n_total > n_visible:
        rejected_count = n_total - n_visible
        st.caption(
            f"Showing {n_visible:,} accepted of {n_total:,} domains "
            f"({rejected_count:,} rejected hidden)."
        )
    if n_visible > _MAX_POINTS:
        st.caption(
            f"Downsampled to {_MAX_POINTS:,} of {n_visible:,} accepted domains "
            f"(selected ids always kept)."
        )

    selected = state.selected_ids
    dragmode = _brushing.get_dragmode(state)
    interaction = state.interaction_mode
    if interaction == _brushing.INTERACTION_SINGLE:
        pane_hint = "click to select · drag to pan · scroll to zoom"
    elif interaction == _brushing.INTERACTION_ZOOM:
        pane_hint = "drag a box to zoom in · scroll to zoom · double-click resets"
    else:
        pane_hint = "lasso to brush · scroll to zoom"

    # Embed interaction mode + axis pair in the chart key so Streamlit
    # treats axis swaps + dragmode flips as fresh widgets and doesn't
    # replay stale lasso payloads onto the rebuilt figure (Task 1 fix).
    suffix = f"{interaction}_{x_axis}_{y_axis}"

    # Two side-by-side panels: 3D RGB (display only — no lasso events) on
    # the left, the user-configurable 2D scatter on the right. The 3D
    # plot orients the user in RGB space; the 2D plot is where actual
    # selection happens.
    col_3d, col_2d = st.columns([1, 1])

    rgb_sub_3d = stats["repr_rgbs"][visible_idx][sub_idx]
    with col_3d:
        st.caption("3D R-G-B (display only)")
        fig3d = _brushing.make_3d_scatter(
            rgb_sub_3d, ids_sub,
            base_colors=base_colors, selected_ids=selected,
            height=500,
        )
        _brushing.render_scatter(
            fig3d, key=f"sel_pane_3d_{suffix}", on_select=False,
        )

    with col_2d:
        st.caption(f"{x_axis} vs {y_axis} ({pane_hint})")
        fig = _brushing.make_2d_scatter(
            x_sub, y_sub, ids_sub,
            base_colors=base_colors, selected_ids=selected,
            x_label=x_axis, y_label=y_axis,
            height=500,
            dragmode=dragmode,
        )
        evt = _brushing.render_scatter(
            fig, key=f"sel_pane_xy_{suffix}", interaction_mode=interaction,
        )
        if _dispatch_event(evt, state):
            st.rerun()


# ─── Flake list table ────────────────────────────────────────────────────

def build_flake_list_df(
    stats: Dict[str, np.ndarray],
    accept_mask: np.ndarray,
    selected_ids: Set[int],
) -> pd.DataFrame:
    """Build the per-domain dataframe shown in the flake list table.

    Marks ``status`` column as ``selected`` for ids in the brushing set,
    ``accepted`` for everything else passing the metric filter, and
    ``rejected`` for filtered-out domains. Pure helper so the export
    buttons can serialise the same rows the user sees.
    """
    flake_ids = stats["flake_ids"].astype(np.int64)
    rgb = stats["repr_rgbs"]
    std = stats["std_pcts"]
    areas = stats["areas"]
    sam2 = stats.get("sam2")
    if sam2 is None:
        sam2 = np.full(len(flake_ids), np.nan)

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
    return df


def _render_flake_list(
    df: pd.DataFrame,
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
    # Newer Streamlit (>= 1.35) supports on_select="rerun" with
    # selection_mode="single-row" on st.dataframe. Older versions raise;
    # gracefully fall back to a non-interactive table so the tab still
    # renders.
    try:
        event = st.dataframe(
            df,
            height=300,
            width="stretch",
            on_select="rerun",
            selection_mode="single-row",
            key="selector_flake_list",
        )
    except (TypeError, ValueError):
        st.dataframe(df, height=300, width="stretch")
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


def _render_export_buttons(
    df_filtered: pd.DataFrame,
    state: _brushing.BrushingState,
) -> None:
    """Export buttons for filtered + brush-selected CSVs.

    The "filtered" download dumps every row currently in the table
    (including rejected/selected status). The "selected" download is
    enabled only when the user has lasso/click-selected at least one
    domain — without a selection it'd be ambiguous whether to dump
    nothing or the accepted set.
    """
    col_a, col_b, _pad = st.columns([2, 2, 6])
    with col_a:
        st.download_button(
            "Export filtered (CSV)",
            data=df_filtered.to_csv(index=False).encode("utf-8"),
            file_name="selector_filtered.csv",
            mime="text/csv",
            key="export_filtered_csv",
        )
    with col_b:
        if state.selected_ids:
            df_selected = df_filtered.loc[
                df_filtered["domain_id"].isin(state.selected_ids)
            ]
            st.download_button(
                "Export selected (CSV)",
                data=df_selected.to_csv(index=False).encode("utf-8"),
                file_name="selector_selected.csv",
                mime="text/csv",
                key="export_selected_csv",
            )
        else:
            st.button(
                "Export selected (CSV)",
                disabled=True,
                help="Lasso some domains first.",
                key="export_selected_csv_disabled",
            )


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
            "Press L (or click Lasso: Replace) for lasso brushing — "
            "use sub-modes Replace/Add/Subtract (R/A/D) to combine selections. "
            "Click a row in the flake list at the bottom to drive the image preview."
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

    # Axis pickers + single 2D scatter (linked brushing)
    x_axis, y_axis = _render_axis_pickers()
    _render_2d_scatter(stats, accept_mask, state, x_axis, y_axis)

    st.caption(f"Brush selected: {len(selected_ids):,}")

    st.divider()

    # Raw image preview — full width, larger height (was implicit ~300).
    focus = _focus_domain_id(state)
    render_image_preview(
        raw_images_dir=raw_images_dir,
        annotations_path=annotations_path,
        domain_id=focus,
        n_selected=len(selected_ids),
        height=600,
    )

    st.divider()

    # Flake list at the bottom + Export buttons.
    st.subheader("Flake list")
    df_filtered = build_flake_list_df(stats, accept_mask, selected_ids)
    _render_export_buttons(df_filtered, state)
    _render_flake_list(df_filtered, state)

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
