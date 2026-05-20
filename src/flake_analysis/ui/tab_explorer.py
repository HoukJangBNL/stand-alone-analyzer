"""Explorer tab — Include/Exclude label picker + flake list + DetailPanel.

Combines ``04_clustering/labels.json`` + ``04_clustering/assignments.parquet``
+ ``05_domain_proximity/flake_assignments.parquet`` to provide an
interactive review of flakes (groups of touching domains) with:

* 3-column Include / Exclude / Available label picker
* NeighborFilter (size, isolation, border-clipped) — size active in PR 2.5
* 2x2 render toggles (Plan v34 defaults; rendering deferred to M3)
* 3-pane Z-layout: substrate grid (LOD 2) · flake list · DetailPanel

Per plan v1 r9 §M2 PR 2.5 + §10 R9 spike (LOD 2 only; LOD 0/1/3 deferred).

Mockup: ``06_tab_explorer.html``.
Qpress reference:
``.agents/tasks/standalone_flake_tool/qpress_explorer_reference.md``.

Image rendering, bbox/outline overlays, Geometry+MaskStats sections are
explicitly deferred to M3 polish.
"""
from __future__ import annotations
import json
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


# ─── Substrate canvas (LOD 2 grid) ───────────────────────────────────────

def _render_substrate_grid(
    filtered: pd.DataFrame, all_df: pd.DataFrame, labels: Dict[str, Any]
) -> None:
    """LOD 2 substrate grid via Plotly heatmap.

    Each cell = one ``image_id`` tile. Color = pass-ratio
    (n_pass / n_total) for that tile under the current filter. The
    selected flake's tile is highlighted with a gold rectangle.
    """
    import plotly.graph_objects as go

    if all_df.empty:
        st.info("No flakes to display.")
        return

    image_ids = sorted(all_df["image_id"].unique().tolist())
    n_imgs = len(image_ids)
    grid_w = max(1, int(np.ceil(np.sqrt(n_imgs))))
    grid_h = max(1, int(np.ceil(n_imgs / grid_w)))

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

    matrix = np.zeros((grid_h, grid_w), dtype=float)
    text = np.full((grid_h, grid_w), "", dtype=object)
    pos_to_image: Dict[Tuple[int, int], int] = {}
    for i, img_id in enumerate(image_ids):
        r, c = divmod(i, grid_w)
        s = summary_idx.loc[img_id]
        n_total = int(s["n_total"])
        n_pass = int(s["n_pass"])
        ratio = (n_pass / n_total) if n_total else 0.0
        matrix[r, c] = ratio
        text[r, c] = (
            f"ix{c:03d}_iy{r:03d}<br>"
            f"img {img_id}<br>{n_pass}/{n_total} pass"
        )
        pos_to_image[(r, c)] = int(img_id)

    fig = go.Figure(
        data=go.Heatmap(
            z=matrix,
            text=text,
            hovertemplate="%{text}<extra></extra>",
            colorscale="Blues",
            showscale=True,
            zmin=0,
            zmax=1,
            xgap=1,
            ygap=1,
        )
    )

    # Highlight the tile for the currently-selected flake (gold border).
    sel_id = st.session_state.get(SELECTED_FLAKE_KEY)
    if sel_id is not None:
        match = all_df.loc[all_df["flake_id"] == int(sel_id)]
        if not match.empty:
            sel_img = int(match["image_id"].iloc[0])
            for (r, c), iid in pos_to_image.items():
                if iid == sel_img:
                    fig.add_shape(
                        type="rect",
                        x0=c - 0.5, x1=c + 0.5,
                        y0=r - 0.5, y1=r + 0.5,
                        line=dict(color="#FFC800", width=3),
                    )
                    break

    fig.update_layout(
        title=f"Substrate grid (LOD 2) · {n_imgs} tiles · {len(filtered)} pass",
        height=500,
        margin=dict(l=10, r=10, t=40, b=10),
        xaxis=dict(visible=False),
        yaxis=dict(visible=False, autorange="reversed"),
    )
    st.plotly_chart(fig, width="stretch", key="explorer_grid")
    st.caption(
        "LOD 2 · color = pass-ratio per tile · gold = selected. "
        "LOD 0/1/3 (per-tile drill-down + bbox overlays) deferred to M3."
    )


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
        _render_substrate_grid(filt_df, all_df, labels)
    with mid:
        st.subheader("Flakes")
        _render_flake_list(filt_df)
    with right:
        _render_detail_panel(filt_df, labels)
