"""stand-alone-analyzer Streamlit entry point (M2 PR 2.2 — Compute tab wired)."""
from __future__ import annotations
import streamlit as st

from flake_analysis.ui.sidebar import render_sidebar
from flake_analysis.ui.tab_compute import render_tab_compute


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
    st.info("2. Selector — placeholder. Wired in PR 2.3.")

with tabs[2]:
    st.info("3. Clustering — placeholder. Wired in PR 2.4.")

with tabs[3]:
    st.info("4. Explorer — placeholder. Wired in PR 2.5.")
