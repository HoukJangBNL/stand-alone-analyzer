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
    INTERACTION_LASSO,
    INTERACTION_SINGLE,
    MODE_ADD,
    MODE_REPLACE,
    MODE_SUBTRACT,
    HISTORY_MAX,
    apply_lasso,
    get_dragmode,
    handle_click_event,
    handle_selection_event,
    push_history,
    redo,
    set_interaction_mode,
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


# ─── v0.1.3: interaction modes (single / lasso) ─────────────────────────

def test_default_interaction_mode_is_single():
    """v0.1.3: new states default to single-pick (left-click selects 1)."""
    s = BrushingState()
    assert s.interaction_mode == INTERACTION_SINGLE
    assert s.focus_id is None


def test_set_interaction_mode_single_to_lasso_preserves_sub_mode():
    """Lasso entry preserves selection + last sub-mode (mode is metadata)."""
    s = BrushingState()
    s.selected_ids = {1, 2, 3}
    s.mode = MODE_ADD
    set_interaction_mode(s, INTERACTION_LASSO)
    assert s.interaction_mode == INTERACTION_LASSO
    assert s.selected_ids == {1, 2, 3}  # preserved
    assert s.mode == MODE_ADD            # preserved through lasso entry


def test_set_interaction_mode_single_resets_sub_mode():
    """Single-pick entry defensively resets stale Add/Subtract sub-modes."""
    s = BrushingState()
    s.selected_ids = {1, 2, 3}
    s.mode = MODE_SUBTRACT
    s.interaction_mode = INTERACTION_LASSO
    set_interaction_mode(s, INTERACTION_SINGLE)
    assert s.interaction_mode == INTERACTION_SINGLE
    assert s.selected_ids == {1, 2, 3}  # selection preserved
    assert s.mode == MODE_REPLACE  # sub-mode reset


def test_set_interaction_mode_unknown_falls_back_to_single():
    s = BrushingState()
    set_interaction_mode(s, "garbage")
    assert s.interaction_mode == INTERACTION_SINGLE


def test_get_dragmode_returns_pan_for_single():
    s = BrushingState()
    s.interaction_mode = INTERACTION_SINGLE
    assert get_dragmode(s) == "pan"


def test_get_dragmode_returns_lasso_for_lasso():
    s = BrushingState()
    s.interaction_mode = INTERACTION_LASSO
    assert get_dragmode(s) == "lasso"


# ─── v0.1.3: handle_click_event (single-pick) ───────────────────────────

def test_handle_click_event_replaces_selection():
    """Single-pick click replaces the selection regardless of mode."""
    s = BrushingState()
    s.mode = MODE_ADD
    s.selected_ids = {1, 2, 3}
    event = {"selection": {"points": [{"customdata": 42}]}}
    assert handle_click_event(event, s) is True
    assert s.selected_ids == {42}


def test_handle_click_event_pushes_history():
    """Click is undoable — must snapshot prior selection."""
    s = BrushingState()
    s.selected_ids = {1, 2, 3}
    event = {"selection": {"points": [{"customdata": 99}]}}
    handle_click_event(event, s)
    assert s.selected_ids == {99}
    assert undo(s) is True
    assert s.selected_ids == {1, 2, 3}


def test_handle_click_event_no_op_on_empty_event():
    s = BrushingState()
    s.selected_ids = {1}
    assert handle_click_event(None, s) is False
    assert handle_click_event({}, s) is False
    assert handle_click_event({"selection": {"points": []}}, s) is False
    # Selection unchanged + no spurious history snapshot.
    assert s.selected_ids == {1}
    assert list(s.history) == []


def test_handle_click_event_picks_min_id_on_overlap():
    """When multiple ids come back (overlapping markers), prefer min."""
    s = BrushingState()
    event = {
        "selection": {
            "points": [
                {"customdata": 17},
                {"customdata": 4},
                {"customdata": 99},
            ]
        }
    }
    handle_click_event(event, s)
    assert s.selected_ids == {4}


# ─── v0.1.3: focus_id field ─────────────────────────────────────────────

def test_focus_id_set_by_row_click():
    """focus_id is a separate 'inspect' concept from brushing selection."""
    s = BrushingState()
    s.selected_ids = {7, 9, 11}
    s.focus_id = 9  # simulating row-click in the flake list
    # Brushing selection unchanged (focus_id is independent state).
    assert s.selected_ids == {7, 9, 11}
    assert s.focus_id == 9


def test_lasso_to_single_pick_dragmode_reverts(monkeypatch):
    """v0.1.4 regression — Lasso → Single-pick must restore dragmode='pan'.

    The original bug: pressing S after using lasso left the chart with
    ``dragmode='lasso'`` because the cached event payload from the prior
    lasso interaction was still in session_state and Streamlit was
    re-using the same chart key. Verify that:

    1. ``set_interaction_mode(state, 'single')`` flips ``interaction_mode``.
    2. ``get_dragmode`` immediately returns ``'pan'``.
    3. Stale ``sel_pane_*`` / ``clu_pane_*`` event payloads are dropped.
    """
    fake_session: dict = {
        "sel_pane_rg": {"selection": {"points": [{"customdata": 7}]}},
        "sel_pane_rb": {"selection": {"points": [{"customdata": 8}]}},
        "clu_pane_gb": {"selection": {"points": [{"customdata": 9}]}},
        "filter_widget_state": "must-not-be-deleted",
    }

    class _SS(dict):
        def keys(self):  # type: ignore[override]
            return list(super().keys())

    ss = _SS(fake_session)
    monkeypatch.setattr(B.st, "session_state", ss)

    s = BrushingState()
    s.interaction_mode = INTERACTION_LASSO
    s.mode = MODE_ADD

    set_interaction_mode(s, INTERACTION_SINGLE)

    assert s.interaction_mode == INTERACTION_SINGLE
    assert s.mode == MODE_REPLACE  # defensive reset
    assert get_dragmode(s) == "pan"

    # Pane event payloads cleared, unrelated session keys preserved.
    assert "sel_pane_rg" not in ss
    assert "sel_pane_rb" not in ss
    assert "clu_pane_gb" not in ss
    assert ss["filter_widget_state"] == "must-not-be-deleted"


def test_get_brushing_state_backfills_new_fields(monkeypatch):
    """A pre-v0.1.3 BrushingState (no interaction_mode/focus_id) is upgraded."""
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

    # Simulate a legacy state object missing the new attributes.
    class _Legacy:
        def __init__(self):
            self.selected_ids = {1, 2}
            from collections import deque
            self.history = deque(maxlen=HISTORY_MAX)
            self.redo_stack = []
            self.mode = MODE_REPLACE

    fake_session["legacy.brushing"] = _Legacy()
    s = B.get_brushing_state("legacy")
    assert hasattr(s, "interaction_mode")
    assert hasattr(s, "focus_id")
    assert s.interaction_mode == INTERACTION_SINGLE
    assert s.focus_id is None
    assert s.selected_ids == {1, 2}
