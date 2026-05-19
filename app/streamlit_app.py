"""stand-alone-analyzer Streamlit entry point (M2 PR 2.5 — Explorer tab wired)."""
from __future__ import annotations
import os
import sys
import streamlit as st

# Print version + commit at startup so the user can confirm which build is
# running. Goes to the streamlit terminal, not the browser. Suppressed if
# STAND_ALONE_NO_BANNER=1.
if not os.environ.get("STAND_ALONE_NO_BANNER"):
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

tab_names = ["1. Compute", "2. Selector", "3. Clustering", "4. Explorer"]
tabs = st.tabs(tab_names)

with tabs[0]:
    render_tab_compute(raw_images_dir, annotations_path, analysis_folder)

with tabs[1]:
    render_tab_selector(raw_images_dir, annotations_path, analysis_folder)

with tabs[2]:
    render_tab_clustering(raw_images_dir, annotations_path, analysis_folder)

with tabs[3]:
    render_tab_explorer(raw_images_dir, annotations_path, analysis_folder)
