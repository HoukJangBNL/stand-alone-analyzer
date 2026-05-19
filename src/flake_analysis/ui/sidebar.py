"""Sidebar UI — analysis_folder + auto-filled raw_images / annotations + status panel.

UX flow:
  1. User enters ``analysis_folder`` (only required input).
  2. If a ``manifest.json`` exists there, ``raw_images_dir`` and
     ``annotations_path`` are pre-filled from the manifest's recorded
     values. Both fields stay editable so the user can override.
  3. New analysis: leave the auto-fields blank and type them in manually.
     They will be persisted to manifest on the first compute step.

Mockup reference: 01_sidebar.html.
"""
from __future__ import annotations
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


def _autofill_from_manifest(analysis_folder: str) -> Tuple[str, str]:
    """Read manifest.json and return (raw_images_dir, annotations_path).

    Returns empty strings if the manifest is missing or fields are unset.
    Errors are swallowed so a malformed manifest doesn't break the sidebar.
    """
    if not analysis_folder:
        return "", ""
    try:
        manifest = load_manifest(analysis_folder)
    except Exception:
        return "", ""
    return (manifest.raw_images_dir or "", manifest.annotations_path or "")


def render_sidebar() -> Tuple[str, str, str]:
    """Render sidebar widgets. Returns (raw_images_dir, annotations_path, analysis_folder)."""
    with st.sidebar:
        st.title("Stand-Alone Analyzer")
        st.caption("M2 PR 2.1 — sidebar + manifest core")

        st.subheader("Project Paths")

        # 1. analysis_folder — primary input. Determines what the manifest knows.
        analysis_folder = st.text_input(
            "analysis_folder/",
            value=st.session_state.get("analysis_folder", ""),
            key="analysis_folder",
            help="Where outputs are written. If a manifest.json exists here, "
                 "the other two fields auto-fill below.",
        )

        # Auto-fill from manifest (if available). The widgets below pick up the
        # default value, but the user is free to override. Streamlit only honors
        # ``value=`` for keys that are not already in session_state, so we set
        # session_state directly when those keys are still empty.
        af_raw, af_ann = _autofill_from_manifest(analysis_folder)
        if af_raw and not st.session_state.get("raw_images_dir"):
            st.session_state["raw_images_dir"] = af_raw
        if af_ann and not st.session_state.get("annotations_path"):
            st.session_state["annotations_path"] = af_ann

        autofilled = bool(af_raw or af_ann)
        if autofilled:
            st.caption("✓ raw_images / annotations.json auto-filled from manifest")

        # 2 + 3. raw_images / annotations — pre-populated from manifest when available.
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

        if st.button("🔄 Reload manifest"):
            # Force re-fetch on next render. Clear the auto-filled keys so the
            # manifest values are picked up freshly.
            st.session_state.pop("raw_images_dir", None)
            st.session_state.pop("annotations_path", None)
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
