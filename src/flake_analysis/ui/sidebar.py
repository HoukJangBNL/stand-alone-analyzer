"""Sidebar UI — 3 path inputs + pipeline status panel.

Mockup reference: 01_sidebar.html.
"""
from __future__ import annotations
from pathlib import Path
from typing import Tuple

import streamlit as st

from flake_analysis.state.manifest import load_manifest, step_status
from flake_analysis.state.paths import PIPELINE_STEPS


_STATUS_BADGE = {
    "not_started": "⬜",
    "done": "✅",
    "stale": "⚠",
}

_STEP_DISPLAY_NAMES = {
    "background": "Background",
    "domain_stats": "Domain Stats",
    "selector": "Selector",
    "clustering": "Clustering",
    "domain_proximity": "Domain Proximity",
    "explorer": "Explorer",
}


def render_sidebar() -> Tuple[str, str, str]:
    """Render sidebar widgets. Returns (raw_images_dir, annotations_path, analysis_folder)."""
    with st.sidebar:
        st.title("Stand-Alone Analyzer")
        st.caption("M2 PR 2.1 — sidebar + manifest core")

        st.subheader("Project Paths")
        raw_images_dir = st.text_input(
            "raw_images/",
            value=st.session_state.get("raw_images_dir", ""),
            key="raw_images_dir",
        )
        annotations_path = st.text_input(
            "annotations.json",
            value=st.session_state.get("annotations_path", ""),
            key="annotations_path",
        )
        analysis_folder = st.text_input(
            "analysis_folder/",
            value=st.session_state.get("analysis_folder", ""),
            key="analysis_folder",
        )

        if st.button("🔄 Reload manifest"):
            st.rerun()

        st.divider()
        st.subheader("Pipeline Status")
        if not analysis_folder:
            st.caption("Set analysis_folder above to load manifest.")
        else:
            try:
                manifest = load_manifest(analysis_folder)
                for step in PIPELINE_STEPS:
                    status = step_status(manifest, step)
                    badge = _STATUS_BADGE[status]
                    name = _STEP_DISPLAY_NAMES[step]
                    st.write(f"{badge} {name}")
            except Exception as e:
                st.error(f"manifest error: {e}")

        st.divider()
        st.caption("v0.1.0a0 (M2 PR 2.1)")

    return raw_images_dir, annotations_path, analysis_folder
