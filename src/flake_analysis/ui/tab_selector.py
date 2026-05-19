"""Selector tab — sidebar-driven controls + side-by-side scatter / image preview.

The 5-metric filter is the actual selection contract; the lasso/box
brushing is for cross-pane inspection only and does not modify metric
ranges.

v0.2.2 layout overhaul (UX consultant Plan A — three quick wins):

* **5-metric range sliders** replace the 5×2 grid of ``number_input``
  pairs. One ``st.slider`` per metric returns ``(min, max)`` and the
  area footprint shrinks to ~1/5 of the previous 10-widget block.
  Canonical values still live in non-widget keys (``filter.<metric>_min``
  / ``filter.<metric>_max``) so values survive mode-button reruns
  (commit bfad752 regression coverage).
* **Side-by-side scatter + raw image preview** via ``st.columns([1, 1])``.
  The 3D R-G-B pane is gated behind a "Show 3D RGB" checkbox (default
  OFF) and renders below the side-by-side row when enabled.
* **Sidebar drawer for controls**: mode / undo-redo / filter presets /
  range sliders / axis pickers / live counters / Commit (mirrored) all
  move to ``st.sidebar.expander("⚙ Selector controls", expanded=True)``.
  The tab body now holds only the scatter, image preview, optional 3D
  pane, the (collapsed) flake list, and a tab-body Commit button.
* **Flake list collapsed by default** in an ``st.expander("Flake list",
  expanded=False)`` so the user can pop it open for row-click navigation
  without it dominating the page.

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


def _on_slider_change(metric_key: str) -> None:
    """Slider drag → push tuple into canonical store + sync number_input keys."""
    rng = st.session_state[f"sel_{metric_key}_range"]
    lo, hi = float(rng[0]), float(rng[1])
    st.session_state[f"filter.{metric_key}_min"] = lo
    st.session_state[f"filter.{metric_key}_max"] = hi
    st.session_state[f"sel_{metric_key}_min_input"] = lo
    st.session_state[f"sel_{metric_key}_max_input"] = hi


def _on_min_input_change(metric_key: str) -> None:
    """Min number_input edit → update canonical + slider (clamped)."""
    new_min = float(st.session_state[f"sel_{metric_key}_min_input"])
    cur_max = float(st.session_state[f"filter.{metric_key}_max"])
    if new_min > cur_max:
        new_min = cur_max
        st.session_state[f"sel_{metric_key}_min_input"] = new_min
    st.session_state[f"filter.{metric_key}_min"] = new_min
    st.session_state[f"sel_{metric_key}_range"] = (new_min, cur_max)


def _on_max_input_change(metric_key: str) -> None:
    """Max number_input edit → update canonical + slider (clamped)."""
    new_max = float(st.session_state[f"sel_{metric_key}_max_input"])
    cur_min = float(st.session_state[f"filter.{metric_key}_min"])
    if new_max < cur_min:
        new_max = cur_min
        st.session_state[f"sel_{metric_key}_max_input"] = new_max
    st.session_state[f"filter.{metric_key}_max"] = new_max
    st.session_state[f"sel_{metric_key}_range"] = (cur_min, new_max)


def _render_filter_controls() -> Dict[str, Optional[float]]:
    """Render 5-metric range sliders + numeric inputs. Returns params dict.

    Each metric exposes both a range slider (coarse drag) and two
    ``number_input`` boxes (precise typing). All three widgets share a
    single canonical store (``filter.<metric>_min`` /
    ``filter.<metric>_max``) and stay in sync via ``on_change`` callbacks.

    Why callbacks (and not the previous "compare to detect which widget
    changed" trick): Streamlit ignores the ``value=`` param when a
    widget's session_state key already has a value, so writing to the
    canonical store after the slider rendered does NOT cause the slider
    bar to move on the next rerun (user-reported regression: "숫자
    업데이트 했는데 바는 업데이트가 안되네"). The callbacks fix this by
    pushing edits into both the canonical store AND the *other*
    widgets' session_state keys before the next rerun, so all three
    widgets re-hydrate from the same value.

    Three session_state surfaces:
        * ``filter.<metric>_<min|max>``    — canonical, non-widget, GC-safe.
        * ``sel_<metric>_range``           — slider widget key (tuple).
        * ``sel_<metric>_<min|max>_input`` — number-input widget keys.

    On every render we force-overwrite the widget keys from the
    canonical store BEFORE the widgets render. This handles three
    cases: (a) initial mount (canonical has the defaults), (b) post-GC
    rerun after a mode-button click (widget keys may have been cleared),
    (c) cross-widget sync (canonical reflects the just-updated value
    from the callback).

    Sentinel-None semantics: when ``mn`` equals the metric's default
    minimum, we set ``params[<metric>_min] = None`` so
    :func:`_apply_filter` skips that bound entirely. Same for max.
    """
    st.subheader("5-metric filter")

    params: Dict[str, Optional[float]] = {}

    for key, label, lo, hi, mn_default, mx_default, step, fmt in _METRIC_DEFS:
        store_min = f"filter.{key}_min"
        store_max = f"filter.{key}_max"
        if store_min not in st.session_state:
            st.session_state[store_min] = float(mn_default)
        if store_max not in st.session_state:
            st.session_state[store_max] = float(mx_default)

        cur_min = float(st.session_state[store_min])
        cur_max = float(st.session_state[store_max])
        if cur_min > cur_max:
            cur_min, cur_max = cur_max, cur_min
            st.session_state[store_min] = cur_min
            st.session_state[store_max] = cur_max

        range_key = f"sel_{key}_range"
        min_input_key = f"sel_{key}_min_input"
        max_input_key = f"sel_{key}_max_input"

        # Force-sync widget session_state from the canonical store BEFORE
        # the widgets render. Without this, a number_input edit's callback
        # would update canonical + range_key, but on the very next rerun
        # the slider widget would still hold its previous tuple in
        # session_state and the bar wouldn't move. Pre-writing here makes
        # the slider use the canonical value.
        st.session_state[range_key] = (cur_min, cur_max)
        st.session_state[min_input_key] = cur_min
        st.session_state[max_input_key] = cur_max

        st.slider(
            label,
            min_value=float(lo),
            max_value=float(hi),
            step=float(step),
            format=fmt,
            key=range_key,
            on_change=_on_slider_change,
            args=(key,),
        )
        in_cols = st.columns(2)
        with in_cols[0]:
            st.number_input(
                "min",
                min_value=float(lo),
                max_value=float(hi),
                step=float(step),
                format=fmt,
                key=min_input_key,
                on_change=_on_min_input_change,
                args=(key,),
                label_visibility="collapsed",
            )
        with in_cols[1]:
            st.number_input(
                "max",
                min_value=float(lo),
                max_value=float(hi),
                step=float(step),
                format=fmt,
                key=max_input_key,
                on_change=_on_max_input_change,
                args=(key,),
                label_visibility="collapsed",
            )

        eff_min = float(st.session_state[store_min])
        eff_max = float(st.session_state[store_max])
        params[f"{key}_min"] = eff_min if eff_min != mn_default else None
        params[f"{key}_max"] = eff_max if eff_max != mx_default else None

    return params


def _clear_filter_session_keys() -> None:
    """Reset all selector filter widgets to defaults."""
    for key, _label, _lo, _hi, mn_default, mx_default, _step, _fmt in _METRIC_DEFS:
        st.session_state[f"filter.{key}_min"] = float(mn_default)
        st.session_state[f"filter.{key}_max"] = float(mx_default)
        st.session_state.pop(f"sel_{key}_range", None)
        st.session_state.pop(f"sel_{key}_min_input", None)
        st.session_state.pop(f"sel_{key}_max_input", None)


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


def _on_x_axis_change() -> None:
    st.session_state["axis.x"] = st.session_state["selector_x_axis"]


def _on_y_axis_change() -> None:
    st.session_state["axis.y"] = st.session_state["selector_y_axis"]


def _on_show_3d_change() -> None:
    st.session_state["selector.show_3d"] = bool(
        st.session_state["selector_show_3d"]
    )


def _render_axis_pickers() -> tuple[str, str]:
    """Render X / Y axis dropdowns side-by-side in two columns.

    Defaults: X=R, Y=G (matches the most informative pane from the legacy
    4-pane layout).

    Persistence: callback-based sync to the canonical store
    (``axis.x`` / ``axis.y``). When Streamlit GCs the widget key after a
    mode-button rerun, we re-seed the widget key from the canonical
    store BEFORE the widget renders so the dropdown shows the
    user's pick (user-reported regression: "B-G 한 상태에서 Subtract
    누르니까 R-G 로 돌아가네"). We must NOT force-overwrite the widget
    key when it already exists, otherwise we'd clobber the value the
    user just submitted in this same rerun.
    """
    if "axis.x" not in st.session_state:
        st.session_state["axis.x"] = AVAILABLE_AXES[0]
    if "axis.y" not in st.session_state:
        st.session_state["axis.y"] = AVAILABLE_AXES[1]

    # Re-seed widget keys ONLY when missing (post-GC rehydration). If
    # the widget key already exists it's the source of truth for this
    # rerun and the on_change callback will mirror it back.
    if "selector_x_axis" not in st.session_state:
        st.session_state["selector_x_axis"] = st.session_state["axis.x"]
    if "selector_y_axis" not in st.session_state:
        st.session_state["selector_y_axis"] = st.session_state["axis.y"]

    pick_cols = st.columns(2)
    with pick_cols[0]:
        x_axis = st.selectbox(
            "X-axis",
            AVAILABLE_AXES,
            key="selector_x_axis",
            on_change=_on_x_axis_change,
        )
    with pick_cols[1]:
        y_axis = st.selectbox(
            "Y-axis",
            AVAILABLE_AXES,
            key="selector_y_axis",
            on_change=_on_y_axis_change,
        )
    return x_axis, y_axis


def _build_scatter_arrays(
    stats: Dict[str, np.ndarray],
    accept_mask: np.ndarray,
    state: _brushing.BrushingState,
    x_axis: str,
    y_axis: str,
) -> Optional[Dict[str, np.ndarray]]:
    """Compute the visible-and-downsampled arrays used by the 2D + 3D panes.

    Both the 2D scatter and the (optional) 3D RGB pane operate on the
    same accepted-+-selected subset of domains, downsampled identically
    so a point that appears in one pane appears in the other. Pulling
    this work out of ``_render_2d_scatter`` lets the 3D pane reuse the
    arrays without recomputing or risking a divergent downsample.

    Returns ``None`` when nothing is visible (caller renders an info
    message instead of empty charts).
    """
    flake_ids_all = stats["flake_ids"].astype(np.int64)
    n_total = len(flake_ids_all)

    visible_mask = accept_mask.copy()
    if state.selected_ids:
        sel_arr = np.fromiter(state.selected_ids, dtype=np.int64)
        visible_mask = visible_mask | np.isin(flake_ids_all, sel_arr)

    visible_idx = np.where(visible_mask)[0]
    n_visible = len(visible_idx)
    if n_visible == 0:
        return None

    flake_ids = flake_ids_all[visible_idx]
    x_full = _values_for_axis(stats, x_axis)[visible_idx]
    y_full = _values_for_axis(stats, y_axis)[visible_idx]
    accepted_visible = accept_mask[visible_idx]
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
        "accepted_sub": accepted_visible[sub_idx],
        "rgb_sub": rgb_visible[sub_idx],
        "n_total": np.int64(n_total),
        "n_visible": np.int64(n_visible),
    }


def _render_2d_scatter(
    arrays: Dict[str, np.ndarray],
    state: _brushing.BrushingState,
    x_axis: str,
    y_axis: str,
    *,
    height: int = 520,
) -> None:
    """Render the configurable 2D scatter alone (no 3D companion).

    The 3D pane is now optional and rendered separately by
    :func:`_render_3d_rgb` so the side-by-side scatter / image-preview
    layout introduced in v0.2.2 stays clean.
    """
    n_total = int(arrays["n_total"])
    n_visible = int(arrays["n_visible"])
    x_sub = arrays["x_sub"]
    y_sub = arrays["y_sub"]
    ids_sub = arrays["ids_sub"]
    accepted_sub = arrays["accepted_sub"]

    # Accepted = green; selected-but-now-rejected = amber.
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

    st.caption(f"{x_axis} vs {y_axis} ({pane_hint})")
    fig = _brushing.make_2d_scatter(
        x_sub, y_sub, ids_sub,
        base_colors=base_colors, selected_ids=selected,
        x_label=x_axis, y_label=y_axis,
        height=height,
        dragmode=dragmode,
    )
    evt = _brushing.render_scatter(
        fig, key=f"sel_pane_xy_{suffix}", interaction_mode=interaction,
    )
    if _dispatch_event(evt, state):
        st.rerun()


def _render_3d_rgb(
    arrays: Dict[str, np.ndarray],
    state: _brushing.BrushingState,
    *,
    height: int = 520,
) -> None:
    """Render the 3D R-G-B context pane (display only, no events)."""
    rgb_sub = arrays["rgb_sub"]
    ids_sub = arrays["ids_sub"]
    accepted_sub = arrays["accepted_sub"]
    base_colors = np.where(accepted_sub, "#43a047", "#fbc02d")

    st.caption("3D R-G-B (display only)")
    fig3d = _brushing.make_3d_scatter(
        rgb_sub, ids_sub,
        base_colors=base_colors, selected_ids=state.selected_ids,
        height=height,
    )
    _brushing.render_scatter(
        fig3d, key="sel_pane_3d", on_select=False,
    )


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


def _commit_selection(
    analysis_folder: str,
    params: Dict[str, Optional[float]],
    state: _brushing.BrushingState,
    *,
    button_key: str,
) -> None:
    """Render the Commit button + run the selector pipeline step on click.

    Commit semantics (clarified per user feedback "commit 시 selected
    된 게 commit 되어야지"):

    * **Filter pass = Accepted.** Domains passing the 5-metric filter.
    * **Lasso brush = Selected.** Domains explicitly picked via lasso.
    * **What gets committed:**
        - If the brush set is empty → all Accepted domains are written
          as ``selected=True`` in selection.parquet (legacy behavior;
          a "filter-only" commit).
        - If the brush set is non-empty → the intersection
          (Accepted ∩ Selected) is written as ``selected=True``,
          everything else ``selected=False``. The intersection prevents
          the user from accidentally committing brushed domains that
          their own filter just rejected.

    The pipeline call (which writes the filter-pass result) runs first,
    then we open the parquet and tighten ``selected`` to the
    intersection if a brush set exists. Pipeline params + manifest
    entry are unchanged so the upstream contract for Clustering /
    Explorer holds.
    """
    if not st.button(
        "✅ Commit selection",
        type="primary",
        help=(
            "Write 03_selector/selection.parquet. If you've lassoed a "
            "subset, that brush ∩ filter is committed; otherwise the "
            "full filter-accepted set is committed."
        ),
        key=button_key,
    ):
        return

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

        # Apply the brush intersection on top of the filter pass.
        # Pipeline-written selection.parquet has columns
        # ``domain_id`` / ``selected``; we tighten ``selected`` to
        # accept ∩ brush when the brush set is non-empty.
        sel_path = Path(str(result["output_path"]))
        n_committed = int(result["selected_count"])
        if state.selected_ids:
            df = pd.read_parquet(sel_path)
            brush_arr = np.fromiter(state.selected_ids, dtype=np.int64)
            in_brush = df["domain_id"].astype(np.int64).isin(brush_arr)
            df["selected"] = df["selected"].astype(bool) & in_brush
            df.to_parquet(sel_path, index=False)
            n_committed = int(df["selected"].sum())

        progress_bar.progress(1.0, "Done")
        st.success(
            f"Committed {n_committed:,} / {result['total_count']:,} "
            f"domains → {sel_path.name}"
        )
        st.rerun()
    except Exception as e:
        st.error(str(e))


def render_selector_sidebar(
    state: _brushing.BrushingState,
    stats: Dict[str, np.ndarray],
    analysis_folder: str,
) -> tuple[Dict[str, Optional[float]], np.ndarray, str, str, bool]:
    """Render the Selector control drawer in the sidebar.

    Adds an ``st.sidebar.expander("⚙ Selector controls", expanded=True)``
    that owns: mode buttons, undo/redo/clear, filter presets, the
    5-metric range sliders, axis pickers, live counters, the "Show 3D
    RGB" toggle, and a mirrored Commit button.

    Returns ``(params, accept_mask, x_axis, y_axis, show_3d)`` so the
    tab body can render the scatter + image preview side-by-side using
    the same filter + axis state the user just configured.

    Streamlit doesn't natively scope sidebar content per tab; this
    helper is invoked only from :func:`render_tab_selector`, so other
    tabs never inject this expander.
    """
    with st.sidebar.expander("⚙ Selector controls", expanded=True):
        # Mode controls + Undo/Redo/Clear (selection history).
        # compact=True reflows the 5-button row into a 2x3 grid + stacked
        # history row so labels don't wrap character-by-character in the
        # narrow ~280px sidebar.
        _brushing.render_mode_controls(state, "selector", compact=True)

        # Filter presets.
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button(
                "✓ Select All",
                help="Clear all bounds (accept everything)",
                key="selector_select_all",
            ):
                _clear_filter_session_keys()
                st.rerun()
        with col_b:
            if st.button(
                "↺ Reset filter",
                help="Reset filter widgets to defaults",
                key="selector_reset_filter",
            ):
                _clear_filter_session_keys()
                st.rerun()

        # 5-metric range sliders.
        params = _render_filter_controls()

        # Live accept/reject preview computed from the just-rendered
        # filter so the counters reflect the user's most recent edit.
        accept_mask = _apply_filter(stats, params)
        n_total = int(len(accept_mask))
        n_accepted = int(accept_mask.sum())
        pct = (100.0 * n_accepted / n_total) if n_total else 0.0
        selected_ids = state.selected_ids

        # Compact one-line counters. "Accepted" = filter pass.
        # "Selected" = lasso brush set (what gets committed if non-empty,
        # otherwise the full Accepted set is committed).
        n_rejected = n_total - n_accepted
        n_brush = len(selected_ids)
        will_commit = (
            n_brush
            if n_brush > 0
            else n_accepted
        )
        st.markdown(
            f"**Accepted** {n_accepted:,} / {n_total:,} "
            f"<span style='color:#43a047'>({pct:.1f}%)</span>  \n"
            f"**Rejected** {n_rejected:,}  \n"
            f"**Selected** (lasso) {n_brush:,}  \n"
            f"**Will commit** {will_commit:,} domains",
            unsafe_allow_html=True,
        )

        st.divider()

        # Axis pickers.
        st.caption("2D scatter axes")
        x_axis, y_axis = _render_axis_pickers()

        # Optional 3D RGB pane. Same canonical-store pattern as the axis
        # pickers — re-seed the widget key only when missing so a user
        # toggle within this rerun isn't clobbered.
        if "selector.show_3d" not in st.session_state:
            st.session_state["selector.show_3d"] = False
        if "selector_show_3d" not in st.session_state:
            st.session_state["selector_show_3d"] = bool(
                st.session_state["selector.show_3d"]
            )
        show_3d = st.checkbox(
            "Show 3D RGB",
            help="Render the 3D R-G-B context pane below the scatter "
                 "(display only — no lasso events).",
            key="selector_show_3d",
            on_change=_on_show_3d_change,
        )

        st.divider()

        # Mirrored Commit button at the bottom of the drawer.
        _commit_selection(
            analysis_folder, params, state,
            button_key="selector_commit_sidebar",
        )

    return params, accept_mask, x_axis, y_axis, show_3d


def render_tab_selector(
    raw_images_dir: str,
    annotations_path: str,
    analysis_folder: str,
) -> None:
    """Render the Selector tab.

    v0.2.2 layout:

    * Sidebar drawer holds all controls (mode, undo/redo/clear, filter
      presets, range sliders, axis pickers, counters, 3D toggle,
      mirrored Commit).
    * Tab body shows: top help banner → scatter ‖ raw image preview
      side-by-side → optional 3D RGB pane → flake list (collapsed) →
      tab-body Commit.
    """
    if not analysis_folder:
        st.warning("Set analysis_folder in sidebar to enable the Selector tab.")
        return

    state = _brushing.get_brushing_state("selector")

    # Inject keyboard shortcut JS + wheel capture (best-effort — Streamlit
    # iframe sandbox may block them; the visible buttons remain primary).
    _brushing.render_keyboard_shortcuts()
    _brushing.render_wheel_capture()

    st.info(
        "**Workflow:** filter (5-metric sliders) narrows the candidate set to "
        "**Accepted**; lasso further picks individual domains as **Selected**. "
        "**Commit** writes Selected to ``selection.parquet`` (or, if no lasso "
        "is active, all Accepted). Clustering / Explorer downstream consume "
        "what was committed here."
    )

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

    # All controls live in the sidebar drawer; this returns the live
    # filter params + accept_mask + axis picks + 3D-toggle so the body
    # can render the scatter + image preview without recomputing.
    params, accept_mask, x_axis, y_axis, show_3d = render_selector_sidebar(
        state, stats, analysis_folder,
    )

    selected_ids = state.selected_ids
    arrays = _build_scatter_arrays(stats, accept_mask, state, x_axis, y_axis)

    # Side-by-side: configurable 2D scatter on the left, raw image
    # preview on the right. Both at height=520 so they line up.
    body_col_l, body_col_r = st.columns([1, 1])
    with body_col_l:
        if arrays is None:
            st.info(
                "No domains pass the current filter. "
                "Loosen the metric ranges (or click ✓ Select All) to see anything."
            )
        else:
            _render_2d_scatter(arrays, state, x_axis, y_axis, height=520)
            st.caption(f"Selected (lasso): {len(selected_ids):,}")

    with body_col_r:
        focus = _focus_domain_id(state)
        render_image_preview(
            raw_images_dir=raw_images_dir,
            annotations_path=annotations_path,
            domain_id=focus,
            n_selected=len(selected_ids),
            height=520,
        )

    # Optional 3D RGB pane below the side-by-side row. Rendered below
    # rather than as a third column because (a) 3D plots benefit from
    # full width, (b) it keeps the side-by-side scatter / image layout
    # uncluttered when the toggle is off (the default).
    if show_3d and arrays is not None:
        st.divider()
        _render_3d_rgb(arrays, state, height=520)

    st.divider()

    # Flake list collapsed by default — pop it open for row-click navigation.
    df_filtered = build_flake_list_df(stats, accept_mask, selected_ids)
    with st.expander("Flake list", expanded=False):
        _render_export_buttons(df_filtered, state)
        _render_flake_list(df_filtered, state)

    st.divider()

    # Tab-body Commit button (mirror of the sidebar drawer's Commit).
    selector_entry = manifest.steps.get("selector")
    if selector_entry and selector_entry.completed_at:
        st.caption(f"Last commit: {selector_entry.completed_at}")
    _commit_selection(
        analysis_folder, params, state,
        button_key="selector_commit_body",
    )
