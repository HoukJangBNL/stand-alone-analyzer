"""Selector tab — 4-pane RGB scatter + 5-metric bidirectional filter + flake list.

This tab is the SPIKE for the linked-brushing pattern (plan v1 r9 §10 R7).
The 5-metric filter is the actual selection contract; the lasso/box
brushing is for cross-pane inspection only and does not modify metric
ranges. Per-domain image preview is deferred to a follow-up (M3).

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


# ─── State helpers ────────────────────────────────────────────────────────

LASSO_KEY = "selector.lasso_selected_ids"


def _get_lasso_ids() -> Set[int]:
    val = st.session_state.get(LASSO_KEY)
    if not val:
        return set()
    return set(val)


def _save_lasso_ids(ids: Set[int]) -> None:
    st.session_state[LASSO_KEY] = ids


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
    """Return boolean mask of accepted domains based on 5-metric bounds.

    Mirrors ``flake_core.pipeline.selector.run_selector`` but operates
    purely in-memory for live preview. Missing ``sam2`` column → bounds
    ignored (allow_missing=True semantics).
    """
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

    A bound equal to its default is treated as ``None`` (no constraint),
    matching the ``run_selector`` semantics.
    """
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


# ─── 4-pane scatter (linked brushing spike) ──────────────────────────────

# Static downsample cap for PR 2.3. Future: zoom-aware (M3).
_MAX_POINTS = 5000


def _downsample_indices(n: int, cap: int = _MAX_POINTS) -> np.ndarray:
    if n <= cap:
        return np.arange(n)
    return np.random.default_rng(0).choice(n, cap, replace=False)


def _make_2d_scatter(
    x: np.ndarray,
    y: np.ndarray,
    ids: np.ndarray,
    colors: np.ndarray,
    sizes: np.ndarray,
    line_colors: np.ndarray,
    x_label: str,
    y_label: str,
):
    import plotly.graph_objects as go

    fig = go.Figure(
        data=go.Scattergl(
            x=x,
            y=y,
            mode="markers",
            marker=dict(
                size=sizes,
                color=colors,
                line=dict(width=1.5, color=line_colors),
            ),
            customdata=ids,
            hovertemplate=(
                f"id=%{{customdata}}<br>{x_label}=%{{x:.0f}}<br>"
                f"{y_label}=%{{y:.0f}}<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        height=300,
        margin=dict(l=10, r=10, t=10, b=30),
        xaxis_title=x_label,
        yaxis_title=y_label,
        dragmode="lasso",
        showlegend=False,
    )
    return fig


def _handle_selection_event(event, key: str) -> None:
    """Save lasso/box selection from a Plotly event into session_state.

    Streamlit ≥1.30 returns an object with ``.selection`` (mapping or
    AttrDict) when ``on_select="rerun"`` is set. We tolerate both
    dict-style and attr-style access plus ``None``.
    """
    if not event:
        return

    selection = None
    if isinstance(event, dict):
        selection = event.get("selection")
    else:
        selection = getattr(event, "selection", None)

    if not selection:
        return

    points = None
    if isinstance(selection, dict):
        points = selection.get("points")
    else:
        points = getattr(selection, "points", None)

    if not points:
        # Empty selection — user cleared the lasso. Only clear on a fresh
        # interaction with this pane (skip if no widgets were touched).
        return

    selected_ids: Set[int] = set()
    for p in points:
        cd = p.get("customdata") if isinstance(p, dict) else getattr(p, "customdata", None)
        if cd is None:
            continue
        try:
            selected_ids.add(int(cd))
        except (TypeError, ValueError):
            continue

    if selected_ids:
        _save_lasso_ids(selected_ids)


def _render_4pane_scatter(
    stats: Dict[str, np.ndarray],
    accept_mask: np.ndarray,
    lasso_ids: Set[int],
) -> None:
    """4-pane RGB scatter (3D + R-G + R-B + G-B) with linked brushing.

    Color encoding: green=accepted, red=rejected.
    Lasso/box-selected points get an orange ring across all panes.
    """
    import plotly.graph_objects as go

    rgb = stats["repr_rgbs"]
    flake_ids = stats["flake_ids"].astype(np.int64)
    n = len(flake_ids)

    sub_idx = _downsample_indices(n)
    rgb_sub = rgb[sub_idx]
    ids_sub = flake_ids[sub_idx]
    accepted_sub = accept_mask[sub_idx]

    colors = np.where(accepted_sub, "#43a047", "#e53935")  # green / red
    is_lasso = np.array([fid in lasso_ids for fid in ids_sub])
    sizes = np.where(is_lasso, 8, 4)
    line_colors = np.where(is_lasso, "#ff9800", "rgba(0,0,0,0)")  # orange ring

    if n > _MAX_POINTS:
        st.caption(
            f"Showing {_MAX_POINTS:,} of {n:,} domains "
            f"(seeded random downsample for plot perf)."
        )

    col1, col2 = st.columns(2)
    with col1:
        st.caption("3D R-G-B scatter")
        fig3d = go.Figure(
            data=go.Scatter3d(
                x=rgb_sub[:, 0],
                y=rgb_sub[:, 1],
                z=rgb_sub[:, 2],
                mode="markers",
                marker=dict(size=3, color=colors),
                customdata=ids_sub,
                hovertemplate=(
                    "domain_id=%{customdata}<br>"
                    "R=%{x:.0f}, G=%{y:.0f}, B=%{z:.0f}<extra></extra>"
                ),
            )
        )
        fig3d.update_layout(
            height=350,
            margin=dict(l=0, r=0, t=20, b=0),
            scene=dict(
                xaxis_title="R",
                yaxis_title="G",
                zaxis_title="B",
            ),
        )
        # Plotly 3D does not support box/lasso selection events — display only.
        st.plotly_chart(fig3d, use_container_width=True, key="sel_pane_3d")

    with col2:
        st.caption("R vs G (lasso/box to brush)")
        fig_rg = _make_2d_scatter(
            rgb_sub[:, 0], rgb_sub[:, 1], ids_sub,
            colors, sizes, line_colors, "R", "G",
        )
        event_rg = st.plotly_chart(
            fig_rg,
            use_container_width=True,
            on_select="rerun",
            selection_mode=("lasso", "box"),
            key="sel_pane_rg",
        )
        _handle_selection_event(event_rg, "sel_pane_rg")

    col3, col4 = st.columns(2)
    with col3:
        st.caption("R vs B (lasso/box to brush)")
        fig_rb = _make_2d_scatter(
            rgb_sub[:, 0], rgb_sub[:, 2], ids_sub,
            colors, sizes, line_colors, "R", "B",
        )
        event_rb = st.plotly_chart(
            fig_rb,
            use_container_width=True,
            on_select="rerun",
            selection_mode=("lasso", "box"),
            key="sel_pane_rb",
        )
        _handle_selection_event(event_rb, "sel_pane_rb")

    with col4:
        st.caption("G vs B (lasso/box to brush)")
        fig_gb = _make_2d_scatter(
            rgb_sub[:, 1], rgb_sub[:, 2], ids_sub,
            colors, sizes, line_colors, "G", "B",
        )
        event_gb = st.plotly_chart(
            fig_gb,
            use_container_width=True,
            on_select="rerun",
            selection_mode=("lasso", "box"),
            key="sel_pane_gb",
        )
        _handle_selection_event(event_gb, "sel_pane_gb")


# ─── Right pane: flake list + image preview placeholder ──────────────────

def _render_flake_list(
    stats: Dict[str, np.ndarray],
    accept_mask: np.ndarray,
    lasso_ids: Set[int],
) -> None:
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
    if lasso_ids:
        df.loc[df["domain_id"].isin(lasso_ids), "status"] = "lasso"

    st.dataframe(df, height=300, use_container_width=True)
    st.caption("Per-domain image preview deferred to follow-up (M3).")


# ─── Top-level renderer ──────────────────────────────────────────────────

def render_tab_selector(
    raw_images_dir: str,
    annotations_path: str,
    analysis_folder: str,
) -> None:
    """Render the Selector tab."""
    if not analysis_folder:
        st.warning("Set analysis_folder in sidebar to enable the Selector tab.")
        return

    st.info(
        "Selector right-pane is mandatory in v1. "
        "Lasso/box select in any 2D pane → highlight same domains in all panes."
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

    # Preset buttons
    col_a, col_b, col_c, _spacer = st.columns([1, 1, 1, 3])
    with col_a:
        if st.button("✓ Select All", help="Clear all bounds (accept everything)"):
            _clear_filter_session_keys()
            _save_lasso_ids(set())
            st.rerun()
    with col_b:
        if st.button("↺ Reset", help="Reset filter widgets to defaults"):
            _clear_filter_session_keys()
            st.rerun()
    with col_c:
        if st.button("✗ Clear lasso", help="Clear cross-pane lasso highlight"):
            _save_lasso_ids(set())
            st.rerun()

    # 5-metric filter widgets
    params = _render_filter_controls()

    # Live accept/reject preview
    accept_mask = _apply_filter(stats, params)
    n_total = int(len(accept_mask))
    n_accepted = int(accept_mask.sum())
    pct = (100.0 * n_accepted / n_total) if n_total else 0.0
    lasso_ids = _get_lasso_ids()

    metric_cols = st.columns(3)
    with metric_cols[0]:
        st.metric("Accepted", f"{n_accepted:,} / {n_total:,}", f"{pct:.1f}%")
    with metric_cols[1]:
        st.metric("Rejected", f"{n_total - n_accepted:,}")
    with metric_cols[2]:
        st.metric("Lasso highlighted", f"{len(lasso_ids):,}")

    st.divider()

    # 4-pane scatter (linked brushing spike)
    _render_4pane_scatter(stats, accept_mask, lasso_ids)

    st.divider()

    # Right-pane: flake list
    st.subheader("Flake list")
    _render_flake_list(stats, accept_mask, lasso_ids)

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
        with st.spinner("Writing selection.parquet..."):
            try:
                result = run_selector_step(analysis_folder=analysis_folder, **params)
                st.success(
                    f"Selection committed: {result['selected_count']:,} / "
                    f"{result['total_count']:,} domains -> {result['output_path']}"
                )
                st.rerun()
            except Exception as e:
                st.error(str(e))
