"""Unit tests for ``flake_analysis.ui._brushing`` (PR v0.1.2).

Covers BrushingState lifecycle, history bounds, undo/redo round-trip,
apply_lasso mode semantics, and selection event extraction. The tests
do not boot Streamlit; they import the helper directly and exercise its
pure-Python pieces.
"""
from __future__ import annotations

import pytest

# A test-time import shim so we don't pull in streamlit during collection.
# render_* functions ARE imported but never invoked, so streamlit's import
# at module load time is unavoidable. The package is in dev deps so this
# is fine; we just don't render anything.
from flake_analysis.ui import _brushing as B
from flake_analysis.ui._brushing import (
    BrushingState,
    MODE_ADD,
    MODE_REPLACE,
    MODE_SUBTRACT,
    HISTORY_MAX,
    apply_lasso,
    handle_selection_event,
    push_history,
    redo,
    undo,
)


# ─── BrushingState basics ────────────────────────────────────────────────

def test_brushing_state_defaults():
    s = BrushingState()
    assert s.selected_ids == set()
    assert list(s.history) == []
    assert s.redo_stack == []
    assert s.mode == MODE_REPLACE


def test_get_brushing_state_lazy_init(monkeypatch):
    """get_brushing_state stores per-prefix state in st.session_state."""
    fake_session: dict = {}

    class _SS:
        def get(self, k, default=None):
            return fake_session.get(k, default)

        def __getitem__(self, k):
            return fake_session[k]

        def __setitem__(self, k, v):
            fake_session[k] = v

        def __contains__(self, k):
            return k in fake_session

    monkeypatch.setattr(B.st, "session_state", _SS())
    s1 = B.get_brushing_state("alpha")
    assert isinstance(s1, BrushingState)
    s2 = B.get_brushing_state("alpha")
    assert s1 is s2  # same object on re-fetch
    s3 = B.get_brushing_state("beta")
    assert s3 is not s1  # different prefix → different state


# ─── push_history / undo / redo ──────────────────────────────────────────

def test_push_history_truncation():
    s = BrushingState()
    # Push HISTORY_MAX + 5 snapshots.
    for i in range(HISTORY_MAX + 5):
        s.selected_ids = {i}
        push_history(s)
    assert len(s.history) == HISTORY_MAX
    # Oldest entries dropped: the first surviving snapshot is i=5.
    first = s.history[0]
    assert first == {5}


def test_push_history_clears_redo():
    s = BrushingState()
    s.redo_stack = [{99}]
    push_history(s)
    assert s.redo_stack == []


def test_undo_redo_round_trip():
    s = BrushingState()
    s.selected_ids = {1, 2, 3}
    apply_lasso(s, {4, 5})  # push history first, then replace
    assert s.selected_ids == {4, 5}
    assert undo(s) is True
    assert s.selected_ids == {1, 2, 3}
    assert redo(s) is True
    assert s.selected_ids == {4, 5}


def test_undo_empty_history_returns_false():
    s = BrushingState()
    assert undo(s) is False
    assert redo(s) is False


def test_undo_chain():
    """Multiple sequential applies should undo back to the start."""
    s = BrushingState()
    apply_lasso(s, {1})  # snap=∅
    apply_lasso(s, {1, 2})  # snap={1}
    apply_lasso(s, {1, 2, 3})  # snap={1,2}
    assert s.selected_ids == {1, 2, 3}
    assert undo(s)
    assert s.selected_ids == {1, 2}
    assert undo(s)
    assert s.selected_ids == {1}
    assert undo(s)
    assert s.selected_ids == set()


# ─── apply_lasso modes ───────────────────────────────────────────────────

def test_apply_lasso_replace():
    s = BrushingState()
    s.mode = MODE_REPLACE
    s.selected_ids = {1, 2, 3}
    apply_lasso(s, {10, 20})
    assert s.selected_ids == {10, 20}


def test_apply_lasso_add():
    s = BrushingState()
    s.mode = MODE_ADD
    s.selected_ids = {1, 2}
    apply_lasso(s, {2, 3})
    assert s.selected_ids == {1, 2, 3}


def test_apply_lasso_subtract():
    s = BrushingState()
    s.mode = MODE_SUBTRACT
    s.selected_ids = {1, 2, 3, 4}
    apply_lasso(s, {2, 4, 99})  # 99 not in current; ignored
    assert s.selected_ids == {1, 3}


def test_apply_lasso_unknown_mode_falls_back_to_replace():
    s = BrushingState()
    s.mode = "garbage"
    s.selected_ids = {1, 2, 3}
    apply_lasso(s, {7, 8})
    assert s.selected_ids == {7, 8}


# ─── clear_selection ─────────────────────────────────────────────────────

def test_clear_selection_pushes_history():
    s = BrushingState()
    s.selected_ids = {1, 2, 3}
    B.clear_selection(s)
    assert s.selected_ids == set()
    assert undo(s)
    assert s.selected_ids == {1, 2, 3}


# ─── handle_selection_event ──────────────────────────────────────────────

def test_handle_selection_event_dict_style():
    s = BrushingState()
    event = {
        "selection": {
            "points": [
                {"customdata": 10},
                {"customdata": 20},
                {"customdata": 20},  # duplicate ignored via set
            ]
        }
    }
    assert handle_selection_event(event, s) is True
    assert s.selected_ids == {10, 20}


def test_handle_selection_event_customdata_list():
    """Plotly sometimes wraps customdata in a 1-element list."""
    s = BrushingState()
    event = {
        "selection": {
            "points": [
                {"customdata": [42]},
                {"customdata": [43]},
            ]
        }
    }
    handle_selection_event(event, s)
    assert s.selected_ids == {42, 43}


def test_handle_selection_event_none_returns_false():
    s = BrushingState()
    assert handle_selection_event(None, s) is False
    assert handle_selection_event({}, s) is False
    assert handle_selection_event({"selection": {"points": []}}, s) is False
    assert s.selected_ids == set()


def test_handle_selection_event_skips_invalid_customdata():
    s = BrushingState()
    event = {
        "selection": {
            "points": [
                {"customdata": "not-an-int"},
                {"customdata": None},
                {"customdata": 7},
            ]
        }
    }
    handle_selection_event(event, s)
    assert s.selected_ids == {7}


def test_handle_selection_event_attr_style():
    """Some Streamlit versions return AttrDict-like objects."""
    class AD:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    s = BrushingState()
    event = AD(selection=AD(points=[AD(customdata=99)]))
    handle_selection_event(event, s)
    assert s.selected_ids == {99}


# ─── SHARED_PLOTLY_CONFIG ───────────────────────────────────────────────

def test_shared_plotly_config_has_scroll_zoom():
    assert B.SHARED_PLOTLY_CONFIG["scrollZoom"] is True
    assert B.SHARED_PLOTLY_CONFIG["displaylogo"] is False
