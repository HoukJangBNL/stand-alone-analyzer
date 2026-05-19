"""stand-alone-analyzer Streamlit entry point (M2 PR 2.5 — Explorer tab wired)."""
from __future__ import annotations
import os
import sys
import streamlit as st

# Print version + commit on the FIRST rerun of each browser session.
# Streamlit re-executes this script on every user interaction, so without
# a guard the banner spams the terminal and drowns out real logs. Goes
# to the streamlit terminal, not the browser. Suppressed by
# STAND_ALONE_NO_BANNER=1.
_BANNER_FLAG = "_stand_alone_banner_printed"
if (
    not os.environ.get("STAND_ALONE_NO_BANNER")
    and not st.session_state.get(_BANNER_FLAG, False)
):
    try:
        from flake_analysis import __version__ as _ver
    except Exception:
        _ver = "?"
    _commit = "?"
    try:
        import subprocess
        _commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        pass
    print(
        f"[stand-alone-analyzer] starting v{_ver} (commit {_commit})",
        file=sys.stderr,
        flush=True,
    )
    st.session_state[_BANNER_FLAG] = True

from flake_analysis.ui.sidebar import render_sidebar
from flake_analysis.ui.tab_clustering import render_tab_clustering
from flake_analysis.ui.tab_compute import render_tab_compute
from flake_analysis.ui.tab_explorer import render_tab_explorer
from flake_analysis.ui.tab_selector import render_tab_selector


st.set_page_config(
    page_title="Stand-Alone Analyzer",
    page_icon=":microscope:",
    layout="wide",
)

raw_images_dir, annotations_path, analysis_folder = render_sidebar()

st.title("Stand-Alone Analyzer")

# Use a top-level radio selector instead of ``st.tabs`` so that only
# the active tab's body and its sidebar drawer render. ``st.tabs``
# evaluates every tab body each rerun, which made the Selector tab's
# sidebar drawer leak into the Clustering / Explorer views (user
# feedback: "셀렉터에서 쓰던 사이드바가 그대로 넘어오는데 탭마다
# 다르게 되어야 하지 않을까"). Radio also persists the active tab via
# its session_state key so reruns don't bounce the user back to "1.
# Compute".
TAB_NAMES = ("1. Compute", "2. Selector", "3. Clustering", "4. Explorer")
active_tab = st.radio(
    "Active tab",
    TAB_NAMES,
    horizontal=True,
    label_visibility="collapsed",
    key="active_tab",
)

if active_tab == TAB_NAMES[0]:
    render_tab_compute(raw_images_dir, annotations_path, analysis_folder)
elif active_tab == TAB_NAMES[1]:
    render_tab_selector(raw_images_dir, annotations_path, analysis_folder)
elif active_tab == TAB_NAMES[2]:
    render_tab_clustering(raw_images_dir, annotations_path, analysis_folder)
elif active_tab == TAB_NAMES[3]:
    render_tab_explorer(raw_images_dir, annotations_path, analysis_folder)
