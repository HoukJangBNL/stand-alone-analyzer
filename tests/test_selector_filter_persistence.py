"""Regression coverage for the Selector filter-value persistence pattern.

Original bug (commit ``bfad752`` / ``b4741aa``): clicking a brushing
mode button (e.g. "Lasso: Replace") triggered a Streamlit rerun that
GC'd the 5-metric ``number_input`` widget keys, wiping the user's typed
filter ranges. The fix mirrors values into non-widget keys
``filter.<metric>_<min|max>`` so they survive the rebuild.

v0.2.2 swapped the 10 ``number_input`` widgets for 5 range
``st.slider`` widgets. The persistence contract is the same — values
must survive a mode-button rerun — but it's now exercised by setting
the slider value to a tuple and asserting the canonical ``filter.*``
keys still hold the user's values after the click. This test guards
that contract end-to-end via ``streamlit.testing.v1.AppTest``.
"""
from __future__ import annotations
import tempfile
from pathlib import Path

import numpy as np
import pytest


def _build_fixture() -> Path:
    """Create a minimal analysis_folder with stats.npz + manifest.json."""
    tmp = Path(tempfile.mkdtemp(prefix="selector_persist_"))
    af = tmp / "analysis"
    (af / "02_domain_stats").mkdir(parents=True)

    n = 100
    rng = np.random.default_rng(0)
    np.savez(
        af / "02_domain_stats" / "stats.npz",
        repr_rgbs=rng.uniform(0, 1, (n, 3)).astype(np.float32),
        std_pcts=rng.uniform(0, 50, (n, 3)).astype(np.float32),
        areas=rng.integers(50, 5000, n).astype(np.float32),
        flake_ids=np.arange(n, dtype=np.int64),
        sam2=rng.uniform(0.5, 1.0, n).astype(np.float32),
    )

    from flake_analysis.state.manifest import Manifest, StepEntry, save_manifest

    m = Manifest(
        analysis_folder=str(af),
        steps={
            "background": StepEntry(
                completed_at="2026-05-19T00:00:00+00:00",
                params={}, params_hash="x",
                outputs={"background_npy": "01_background/background.npy"},
            ),
            "domain_stats": StepEntry(
                completed_at="2026-05-19T00:00:00+00:00",
                params={}, params_hash="y",
                outputs={"stats_npz": "02_domain_stats/stats.npz"},
            ),
        },
    )
    save_manifest(m, af)
    return af


def _set_analysis_folder(at, folder: str) -> None:
    """Drive the sidebar text_input that owns analysis_folder."""
    matches = [w for w in at.text_input if "analysis_folder" in (w.label or "")]
    if matches:
        matches[0].set_value(folder).run()
    else:
        at.session_state["analysis_folder"] = folder
        at.run()


def _click_button(at, text: str) -> None:
    matches = [b for b in at.button if text in (b.label or "")]
    assert matches, f"button containing '{text}' not found; have: {[b.label for b in at.button]}"
    matches[0].click().run()


def test_selector_filter_value_persists_across_mode_button_click():
    """area_min set via the slider must survive a Lasso-mode-button click.

    This is the v0.2.2 reincarnation of the bug fix from bfad752: the
    canonical ``filter.area_min`` should still hold the user's typed
    value after a rerun triggered by a mode-button click.
    """
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest

    af = _build_fixture()
    app_path = Path(__file__).parent.parent / "app" / "streamlit_app.py"

    at = AppTest.from_file(str(app_path), default_timeout=60)
    at.run()

    # Wire the analysis_folder so the Selector tab's preconditions pass.
    _set_analysis_folder(at, str(af))

    # Drive the Area range slider directly. Streamlit's slider with both
    # ``key=`` and ``value=`` uses the existing session_state entry once
    # it's been written by a prior run, so simply overwriting
    # ``filter.area_min`` outside the widget would be reverted on the
    # next render. ``set_value`` simulates an actual user drag and is
    # the canonical way to mutate widget state in AppTest.
    sliders = [s for s in at.slider if "Area" in (s.label or "")]
    assert sliders, (
        f"Area slider not found; sliders present: {[s.label for s in at.slider]}"
    )
    sliders[0].set_value((1500.0, 1_000_000.0)).run()

    assert at.session_state["filter.area_min"] == 1500.0, (
        f"slider drag didn't update filter.area_min "
        f"(got {at.session_state.get('filter.area_min')!r})"
    )

    # Click "Lasso: Replace" — historically this was the rerun that wiped
    # number_input values (commit bfad752). The drawer expander is
    # expanded by default so the button is in the rendered tree.
    _click_button(at, "Lasso: Replace")

    # The canonical store key must survive the mode-button rerun.
    assert at.session_state["filter.area_min"] == 1500.0, (
        f"filter.area_min was lost across mode-button rerun "
        f"(got {at.session_state.get('filter.area_min')!r})"
    )
