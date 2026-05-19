"""Compute tab — 3 expanders: Background / Domain Stats / Domain Proximity.

Per plan v1 r9 §M2 PR 2.2.
"""
from __future__ import annotations
from pathlib import Path

import streamlit as st

from flake_analysis.pipeline.background import run_background_step
from flake_analysis.pipeline.domain_proximity import run_domain_proximity_step
from flake_analysis.pipeline.domain_stats import run_domain_stats_step
from flake_analysis.state.manifest import load_manifest


def render_tab_compute(
    raw_images_dir: str,
    annotations_path: str,
    analysis_folder: str,
) -> None:
    """Render the Compute tab: 3 vertically-stacked expanders + Run All."""

    if not all([raw_images_dir, annotations_path, analysis_folder]):
        st.warning("Set all 3 paths in the sidebar to enable Compute tab.")
        return

    # ─── Run All ────────────────────────────────────────────
    col_run_all, col_caption = st.columns([1, 3])
    with col_run_all:
        run_all = st.button("▶ Run All", type="primary", use_container_width=True)
    with col_caption:
        st.caption(
            "Run Background → Domain Stats → Domain Proximity in order with default params."
        )

    if run_all:
        # Three separate progress bars — one per step. Each shows its own
        # 0% → 100% so the user can see which step is currently active and
        # how far each one got. The earlier "combined" single-bar UX was
        # ambiguous about which step was running.
        st.markdown("**1. Background**")
        bg_bar = st.progress(0.0, "Pending...")
        st.markdown("**2. Domain Stats**")
        ds_bar = st.progress(0.0, "Pending...")
        st.markdown("**3. Domain Proximity**")
        dp_bar = st.progress(0.0, "Pending...")

        def make_cb(bar):
            def cb(pct: float, msg: str) -> None:
                bar.progress(pct, msg)
            return cb

        try:
            run_background_step(
                raw_images_dir=raw_images_dir,
                analysis_folder=analysis_folder,
                progress_callback=make_cb(bg_bar),
            )
            bg_bar.progress(1.0, "Done")
            run_domain_stats_step(
                raw_images_dir=raw_images_dir,
                annotations_path=annotations_path,
                analysis_folder=analysis_folder,
                progress_callback=make_cb(ds_bar),
            )
            ds_bar.progress(1.0, "Done")
            run_domain_proximity_step(
                annotations_path=annotations_path,
                analysis_folder=analysis_folder,
                progress_callback=make_cb(dp_bar),
            )
            dp_bar.progress(1.0, "Done")
            st.success("All 3 compute steps completed.")
            st.rerun()
        except Exception as e:
            st.error(f"Run All failed: {e}")

    # Re-load manifest for status display
    manifest = load_manifest(analysis_folder)
    bg_done = manifest.steps.get("background")
    stats_done = manifest.steps.get("domain_stats")
    prox_done = manifest.steps.get("domain_proximity")

    bg_complete = bool(bg_done and bg_done.completed_at)
    stats_complete = bool(stats_done and stats_done.completed_at)
    prox_complete = bool(prox_done and prox_done.completed_at)

    st.divider()

    # ─── Section 1: Background ──────────────────────────────
    with st.expander(
        f"1. Background  {'✅ done' if bg_complete else '⬜ not started'}",
        expanded=not bg_complete,
    ):
        col1, col2 = st.columns(2)
        with col1:
            seed = st.number_input("seed", value=0, step=1, key="bg_seed")
            max_images = st.number_input(
                "max_images", value=100, step=10, min_value=1, key="bg_max_images"
            )
        with col2:
            gaussian_sigma = st.number_input(
                "gaussian_sigma",
                value=10.0,
                step=0.5,
                min_value=0.0,
                key="bg_sigma",
            )
            method = st.selectbox("method", ["median", "mean"], key="bg_method")

        if st.button("▶ Compute background", key="bg_compute"):
            progress_bar = st.progress(0.0, "Starting...")
            status = st.empty()

            def cb(pct: float, msg: str) -> None:
                progress_bar.progress(pct, msg)
                status.caption(msg)

            try:
                result = run_background_step(
                    raw_images_dir=raw_images_dir,
                    analysis_folder=analysis_folder,
                    seed=int(seed),
                    max_images=int(max_images),
                    gaussian_sigma=float(gaussian_sigma),
                    method=method,
                    progress_callback=cb,
                )
                progress_bar.progress(1.0, "Done")
                st.success(f"Background written to {result['output_path']}")
                st.rerun()
            except Exception as e:
                st.error(str(e))

        if bg_complete:
            st.caption(f"Last run: {bg_done.completed_at}")
            bg_path = Path(analysis_folder) / "01_background" / "background.npy"
            if bg_path.exists():
                try:
                    import numpy as np

                    bg = np.load(bg_path)
                    # downsample for preview speed
                    preview = bg[::4, ::4]
                    if preview.dtype != np.uint8:
                        # normalize to 0..255 for display
                        p = preview.astype(np.float32)
                        p_min, p_max = float(p.min()), float(p.max())
                        if p_max > p_min:
                            p = (p - p_min) / (p_max - p_min) * 255.0
                        preview = p.astype(np.uint8)
                    st.image(
                        preview,
                        caption="background.npy (downsampled preview)",
                        clamp=True,
                    )
                except Exception as e:
                    st.caption(f"(preview unavailable: {e})")

    # ─── Section 2: Domain Stats ────────────────────────────
    bg_required = not bg_complete
    with st.expander(
        f"2. Domain Stats  {'✅ done' if stats_complete else '⬜ not started'}",
        expanded=not stats_complete and not bg_required,
    ):
        if bg_required:
            st.warning("Requires Background step. Run Section 1 first.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                repr_mode = st.selectbox(
                    "repr_mode", ["median", "mean"], key="ds_repr"
                )
            with col2:
                raw_ext = st.text_input("raw_ext", value=".png", key="ds_raw_ext")

            if st.button(
                "▶ Compute stats", key="ds_compute", disabled=bg_required
            ):
                progress_bar = st.progress(0.0, "Starting...")
                status = st.empty()

                def cb(pct: float, msg: str) -> None:
                    progress_bar.progress(pct, msg)
                    status.caption(msg)

                try:
                    result = run_domain_stats_step(
                        raw_images_dir=raw_images_dir,
                        annotations_path=annotations_path,
                        analysis_folder=analysis_folder,
                        repr_mode=repr_mode,
                        raw_ext=raw_ext,
                        progress_callback=cb,
                    )
                    progress_bar.progress(1.0, "Done")
                    st.success(
                        f"Domain stats written: {result['output_path']} "
                        f"({result['num_flakes']} flakes)"
                    )
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

            if stats_complete:
                st.caption(f"Last run: {stats_done.completed_at}")

    # ─── Section 3: Domain Proximity ────────────────────────
    with st.expander(
        f"3. Domain Proximity  {'✅ done' if prox_complete else '⬜ not started'}",
        expanded=not prox_complete,
    ):
        st.caption("Independent step — only needs annotations.json.")
        col1, col2 = st.columns(2)
        with col1:
            r_max_px = st.number_input(
                "r_max_px", value=200.0, step=10.0, min_value=0.0, key="dp_r_max"
            )
            d_touch_px = st.number_input(
                "d_touch_px",
                value=2.0,
                step=0.5,
                min_value=0.0,
                key="dp_d_touch",
            )
            link_distance_um = st.number_input(
                "link_distance_um",
                value=5.0,
                step=0.5,
                min_value=0.0,
                key="dp_link",
            )
        with col2:
            min_area_px_dp = st.number_input(
                "min_area_px",
                value=10,
                step=5,
                min_value=0,
                key="dp_min_area",
            )
            pixel_size_um = st.number_input(
                "pixel_size_um",
                value=0.5,
                step=0.05,
                min_value=0.0,
                format="%.4f",
                key="dp_pixel_size",
            )
            workers = st.number_input(
                "workers", value=4, step=1, min_value=1, key="dp_workers"
            )

        if st.button(
            "▶ Compute pair distances + flakes", key="dp_compute"
        ):
            progress_bar = st.progress(0.0, "Starting...")
            status = st.empty()

            def cb(pct: float, msg: str) -> None:
                progress_bar.progress(pct, msg)
                status.caption(msg)

            try:
                result = run_domain_proximity_step(
                    annotations_path=annotations_path,
                    analysis_folder=analysis_folder,
                    r_max_px=float(r_max_px),
                    d_touch_px=float(d_touch_px),
                    link_distance_um=float(link_distance_um),
                    min_area_px=int(min_area_px_dp),
                    pixel_size_um=float(pixel_size_um),
                    workers=int(workers),
                    progress_callback=cb,
                )
                progress_bar.progress(1.0, "Done")
                st.success(
                    f"{result['n_pairs']} pairs / {result['n_flakes']} flakes "
                    f"across {result['n_domains']} domains"
                )
                st.rerun()
            except Exception as e:
                st.error(str(e))

        if prox_complete:
            st.caption(f"Last run: {prox_done.completed_at}")
