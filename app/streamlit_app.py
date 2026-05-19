"""stand-alone-analyzer Streamlit entry point (M2 PR 2.5 — Explorer tab wired)."""
from __future__ import annotations

# Streamlit 1.57 spams stderr with deprecation notices for st.components.v1.html
# (still the only inline-HTML/JS escape hatch — there is no 1:1 replacement)
# and for use_container_width on widgets we don't directly construct (e.g.
# inside st.dataframe). Hide both so the terminal stays readable for our own
# debug prints. They remain valid until 2026-06-01 / 2025-12-31 respectively.
import warnings as _warnings

_warnings.filterwarnings(
    "ignore",
    message=".*use_container_width.*",
)
_warnings.filterwarnings(
    "ignore",
    message=".*st.components.v1.html.*",
)

# Streamlit logs via stdlib logging too — silence the matching deprecation
# WARN records on the streamlit logger so they don't leak to stderr.
import logging as _logging


class _DeprecationFilter(_logging.Filter):
    def filter(self, record: _logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "use_container_width" in msg:
            return False
        if "st.components.v1.html" in msg:
            return False
        return True


for _name in ("streamlit", "streamlit.runtime"):
    _logging.getLogger(_name).addFilter(_DeprecationFilter())

import streamlit as st

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
