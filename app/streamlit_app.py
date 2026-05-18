"""stand-alone-analyzer Streamlit entry point (M2 PR 2.1)."""
from __future__ import annotations
import streamlit as st

from flake_analysis.ui.sidebar import render_sidebar


st.set_page_config(
    page_title="Stand-Alone Analyzer",
    page_icon=":microscope:",
    layout="wide",
)

raw_images_dir, annotations_path, analysis_folder = render_sidebar()

st.title("Stand-Alone Analyzer")
st.caption("Pipeline tabs (M2 PR 2.2+ wires real content)")

tab_names = [
    "1. Background",
    "2. Domain Stats",
    "3. Selector",
    "4. Clustering",
    "5. Domain Proximity",
    "6. Explorer",
]
tabs = st.tabs(tab_names)

for tab, name in zip(tabs, tab_names):
    with tab:
        st.info(f"{name} — placeholder. Wired in subsequent PRs.")
