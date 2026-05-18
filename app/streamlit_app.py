"""stand-alone-analyzer Streamlit entry point.

For M0, this is just a Hello World page. Subsequent milestones (M3) wire up
sidebar (3 paths + manifest) and 6 pipeline tabs.
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="Stand-Alone Analyzer",
    page_icon=":microscope:",
    layout="wide",
)

st.title("Stand-Alone Analyzer")
st.caption("M0 skeleton. Pipeline tabs wired up in M3.")

st.markdown(
    """
    **Status**: Hello World page (Milestone 0)

    This is the entry point for the standalone flake-analysis Streamlit app.
    Subsequent milestones will add:

    - **M1**: `flake-analysis-core` extraction (sibling repo)
    - **M2**: Qpress migration to use the shared package
    - **M3**: Sidebar (3 paths + manifest) + 6 pipeline tabs
    - **M4**: Parity validation harness
    - **M5**: Polish + GitHub release

    See `plan_v1.md` for full milestone breakdown.
    """
)

st.divider()

st.subheader("Planned tab layout")
st.markdown(
    """
    1. Background — compute median background from raw images
    2. Domain Stats — compute per-domain RGB stats (compute-only)
    3. Selector — 5-metric bidirectional filter with 4-pane scatter
    4. Clustering — manual seed-group GMM with per-cluster thresholds
    5. Domain Proximity — pair distance + flake construction (union-find)
    6. Explorer — substrate grid + LOD + Include/Exclude label picker
    """
)
