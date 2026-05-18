"""Shared brushing scaffolding for cross-pane scatter selection.

This helper extracts the inline 4-pane scatter / lasso pattern that was
duplicated between ``tab_selector`` and ``tab_clustering`` (PR 2.3 / 2.4).

Public API:
    BrushingState                 — per-tab session_state-backed object
    get_brushing_state(prefix)    — lazy init + return
    push_history / undo / redo    — selection history (bounded deque, 20)
    apply_lasso(state, ids)       — combine new lasso ids per current mode
    handle_selection_event(event, state) — extract customdata ids
    render_mode_controls(state, prefix)  — radio + Undo/Redo/Clear buttons
    make_2d_scatter(...) / make_3d_scatter(...)
    SHARED_PLOTLY_CONFIG          — scrollZoom + display tweaks
    render_scatter(fig, key, ...) — st.plotly_chart wrapper
    render_keyboard_shortcuts()   — best-effort JS keymap

Modes:
    MODE_REPLACE — next lasso replaces selected_ids
    MODE_ADD     — next lasso union with selected_ids
    MODE_SUBTRACT — next lasso removed from selected_ids

Per plan: keyboard shortcuts are best-effort. Streamlit's iframe sandbox
may block the cross-frame ``document`` access used here. The mode radio
buttons remain the primary control surface.
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, List, Optional, Set

import numpy as np
import streamlit as st


# ─── Mode constants ──────────────────────────────────────────────────────

MODE_REPLACE = "replace"
MODE_ADD = "add"
MODE_SUBTRACT = "subtract"

ALL_MODES = (MODE_REPLACE, MODE_ADD, MODE_SUBTRACT)
HISTORY_MAX = 20


# ─── Plotly defaults ─────────────────────────────────────────────────────

SHARED_PLOTLY_CONFIG = {
    "scrollZoom": True,
    "displayModeBar": True,
    "displaylogo": False,
    "modeBarButtonsToRemove": ["toImage", "autoScale2d"],
}


# ─── BrushingState ───────────────────────────────────────────────────────

@dataclass
class BrushingState:
    """Per-tab brushing state (selection + history + mode).

    Stored under a single session_state slot keyed ``f"{prefix}.brushing"``.
    The ``history`` and ``redo_stack`` are bounded to ``HISTORY_MAX`` entries.
    """

    selected_ids: Set[int] = field(default_factory=set)
    history: Deque[Set[int]] = field(default_factory=lambda: deque(maxlen=HISTORY_MAX))
    redo_stack: List[Set[int]] = field(default_factory=list)
    mode: str = MODE_REPLACE


def _state_key(prefix: str) -> str:
    return f"{prefix}.brushing"


def get_brushing_state(key_prefix: str) -> BrushingState:
    """Lazy-init session_state-backed BrushingState for a tab."""
    key = _state_key(key_prefix)
    state = st.session_state.get(key)
    if state is None:
        state = BrushingState()
        st.session_state[key] = state
    return state


def push_history(state: BrushingState) -> None:
    """Snapshot current selected_ids into history. Trim to last HISTORY_MAX.

    Calling this before mutating selected_ids enables undo. We always clear
    the redo_stack on a fresh push so redo only works on the most recent
    undo chain.
    """
    snapshot = set(state.selected_ids)
    state.history.append(snapshot)
    state.redo_stack.clear()


def undo(state: BrushingState) -> bool:
    """Pop last history entry into selected_ids. Push current to redo_stack.

    Returns True if undo happened, False if history is empty.
    """
    if not state.history:
        return False
    state.redo_stack.append(set(state.selected_ids))
    state.selected_ids = state.history.pop()
    return True


def redo(state: BrushingState) -> bool:
    """Pop redo_stack into selected_ids. Push current to history.

    Returns True if redo happened, False if redo_stack is empty.
    """
    if not state.redo_stack:
        return False
    state.history.append(set(state.selected_ids))
    state.selected_ids = state.redo_stack.pop()
    return True


def apply_lasso(state: BrushingState, lasso_ids: Set[int]) -> None:
    """Combine ``lasso_ids`` with current selection per state.mode.

    Pushes a history snapshot first. ``lasso_ids`` is treated as the new
    primitive selection from the most recent box/lasso interaction.
    """
    push_history(state)
    if state.mode == MODE_ADD:
        state.selected_ids = state.selected_ids | set(lasso_ids)
    elif state.mode == MODE_SUBTRACT:
        state.selected_ids = state.selected_ids - set(lasso_ids)
    else:  # MODE_REPLACE (default + fallback)
        state.selected_ids = set(lasso_ids)


def clear_selection(state: BrushingState) -> None:
    """Clear selected_ids (with history snapshot for undo)."""
    push_history(state)
    state.selected_ids = set()


# ─── Selection event extraction ─────────────────────────────────────────

def _extract_ids_from_event(event: Any) -> Optional[Set[int]]:
    """Pull customdata ids from a Streamlit plotly selection event.

    Tolerates dict/AttrDict + None. Returns None if the event has no
    selection / no points (caller decides whether to interpret as
    "user cleared" or "no interaction").
    """
    if not event:
        return None
    selection = event.get("selection") if isinstance(event, dict) else getattr(event, "selection", None)
    if not selection:
        return None
    points = (
        selection.get("points")
        if isinstance(selection, dict)
        else getattr(selection, "points", None)
    )
    if not points:
        return None

    ids: Set[int] = set()
    for p in points:
        cd = p.get("customdata") if isinstance(p, dict) else getattr(p, "customdata", None)
        if cd is None:
            continue
        # customdata may be wrapped in a list (single column)
        if isinstance(cd, (list, tuple, np.ndarray)) and len(cd) > 0:
            cd = cd[0]
        try:
            ids.add(int(cd))
        except (TypeError, ValueError):
            continue
    return ids


def handle_selection_event(event: Any, state: BrushingState) -> bool:
    """Apply a Plotly selection event to ``state`` per current mode.

    Returns True if state was modified (caller should not rerun otherwise).
    """
    ids = _extract_ids_from_event(event)
    if ids is None:
        return False
    if not ids:
        # Empty point list — treat as no interaction (user opened modebar etc).
        return False
    apply_lasso(state, ids)
    return True


# ─── Mode controls ───────────────────────────────────────────────────────

# Button labels — kept ASCII so the keyboard-shortcut JS innerText match
# stays robust. (Emoji glyphs proved brittle across Streamlit versions.)
_BTN_REPLACE = "Mode: Replace"
_BTN_ADD = "Mode: Add"
_BTN_SUBTRACT = "Mode: Subtract"
_BTN_UNDO = "Undo"
_BTN_REDO = "Redo"
_BTN_CLEAR = "Clear"


def render_mode_controls(state: BrushingState, key_prefix: str) -> None:
    """Render mode-toggle buttons + Undo/Redo/Clear + status caption.

    The buttons are rendered with stable ASCII labels (see ``_BTN_*``) so
    the keyboard-shortcut JS (``render_keyboard_shortcuts``) can match them
    by ``innerText`` reliably.
    """
    cols = st.columns([1, 1, 1, 1, 1, 1, 3])

    with cols[0]:
        if st.button(
            _BTN_REPLACE,
            key=f"{key_prefix}_mode_replace",
            type="primary" if state.mode == MODE_REPLACE else "secondary",
            help="Next lasso/box replaces the current selection (S)",
        ):
            state.mode = MODE_REPLACE
            st.rerun()
    with cols[1]:
        if st.button(
            _BTN_ADD,
            key=f"{key_prefix}_mode_add",
            type="primary" if state.mode == MODE_ADD else "secondary",
            help="Next lasso/box adds to the current selection (A)",
        ):
            state.mode = MODE_ADD
            st.rerun()
    with cols[2]:
        if st.button(
            _BTN_SUBTRACT,
            key=f"{key_prefix}_mode_subtract",
            type="primary" if state.mode == MODE_SUBTRACT else "secondary",
            help="Next lasso/box subtracts from the current selection (D)",
        ):
            state.mode = MODE_SUBTRACT
            st.rerun()
    with cols[3]:
        if st.button(
            _BTN_UNDO,
            key=f"{key_prefix}_undo",
            help="Undo last selection change (Ctrl/Cmd+Z)",
            disabled=not state.history,
        ):
            undo(state)
            st.rerun()
    with cols[4]:
        if st.button(
            _BTN_REDO,
            key=f"{key_prefix}_redo",
            help="Redo (Ctrl/Cmd+Shift+Z)",
            disabled=not state.redo_stack,
        ):
            redo(state)
            st.rerun()
    with cols[5]:
        if st.button(
            _BTN_CLEAR,
            key=f"{key_prefix}_clear",
            help="Clear current selection (Esc)",
            disabled=not state.selected_ids,
        ):
            clear_selection(state)
            st.rerun()
    with cols[6]:
        st.caption(
            f"Mode: **{state.mode}** · "
            f"selected={len(state.selected_ids):,} · "
            f"history={len(state.history)} · redo={len(state.redo_stack)}"
        )


# ─── Scatter builders ───────────────────────────────────────────────────

def make_2d_scatter(
    x: np.ndarray,
    y: np.ndarray,
    ids: np.ndarray,
    *,
    base_colors: np.ndarray,
    selected_ids: Set[int],
    x_label: str,
    y_label: str,
    height: int = 300,
):
    """Build a Plotly Scattergl figure with selection ring on selected ids.

    ``base_colors`` is a per-point color array (e.g. red/green for accept/
    reject, or cluster palette). Points whose id is in ``selected_ids``
    get a slightly larger size + an orange ring.
    """
    import plotly.graph_objects as go

    is_selected = np.array([int(fid) in selected_ids for fid in ids])
    sizes = np.where(is_selected, 8, 4)
    line_colors = np.where(is_selected, "#ff9800", "rgba(0,0,0,0)")

    fig = go.Figure(
        data=go.Scattergl(
            x=x,
            y=y,
            mode="markers",
            marker=dict(
                size=sizes,
                color=base_colors,
                line=dict(width=1.5, color=line_colors),
            ),
            customdata=ids,
            hovertemplate=(
                f"id=%{{customdata}}<br>{x_label}=%{{x:.0f}}<br>"
                f"{y_label}=%{{y:.0f}}<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=10, b=30),
        xaxis_title=x_label,
        yaxis_title=y_label,
        dragmode="lasso",
        showlegend=False,
    )
    return fig


def make_3d_scatter(
    rgb: np.ndarray,
    ids: np.ndarray,
    *,
    base_colors: np.ndarray,
    selected_ids: Set[int],
    height: int = 350,
):
    """3D RGB scatter (display-only — no lasso event support)."""
    import plotly.graph_objects as go

    is_selected = np.array([int(fid) in selected_ids for fid in ids])
    # Plotly 3D markers don't support per-point line color — emulate the
    # "ring" with a slightly larger marker for selected points.
    sizes = np.where(is_selected, 5, 3)

    fig = go.Figure(
        data=go.Scatter3d(
            x=rgb[:, 0],
            y=rgb[:, 1],
            z=rgb[:, 2],
            mode="markers",
            marker=dict(size=sizes, color=base_colors),
            customdata=ids,
            hovertemplate=(
                "domain_id=%{customdata}<br>"
                "R=%{x:.0f}, G=%{y:.0f}, B=%{z:.0f}<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        height=height,
        margin=dict(l=0, r=0, t=20, b=0),
        scene=dict(xaxis_title="R", yaxis_title="G", zaxis_title="B"),
    )
    return fig


# ─── Render wrapper ─────────────────────────────────────────────────────

def render_scatter(fig, key: str, *, on_select: bool = True):
    """Render st.plotly_chart with shared config + (optional) selection events.

    Returns the streamlit event object (or the chart handle if on_select=False).
    """
    if on_select:
        return st.plotly_chart(
            fig,
            config=SHARED_PLOTLY_CONFIG,
            on_select="rerun",
            selection_mode=("lasso", "box"),
            use_container_width=True,
            key=key,
        )
    return st.plotly_chart(
        fig,
        config=SHARED_PLOTLY_CONFIG,
        use_container_width=True,
        key=key,
    )


# ─── Keyboard shortcuts (best-effort JS injection) ─────────────────────

_KEYBOARD_JS = """
<script>
(function() {
  // Best-effort keyboard shortcuts. Streamlit renders the app inside an
  // iframe; we reach into window.parent.document to find the buttons.
  // Cross-origin sandboxing CAN block this — if so, the radio/buttons
  // remain the primary mechanism.
  try {
    var doc = window.parent.document;
    if (!doc || doc.__brushingShortcutsBound) return;
    doc.__brushingShortcutsBound = true;

    var clickByLabel = function(label) {
      var btns = doc.querySelectorAll('button');
      for (var i = 0; i < btns.length; i++) {
        var b = btns[i];
        if ((b.innerText || '').trim() === label) { b.click(); return true; }
      }
      return false;
    };

    var onKey = function(e) {
      var tag = (e.target && e.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      var key = e.key || '';
      if ((e.ctrlKey || e.metaKey) && key.toLowerCase() === 'z' && e.shiftKey) {
        if (clickByLabel('Redo')) { e.preventDefault(); }
      } else if ((e.ctrlKey || e.metaKey) && key.toLowerCase() === 'z') {
        if (clickByLabel('Undo')) { e.preventDefault(); }
      } else if (key === 'Escape') {
        clickByLabel('Clear');
      } else if (key === 's' || key === 'S') {
        clickByLabel('Mode: Replace');
      } else if (key === 'a' || key === 'A') {
        clickByLabel('Mode: Add');
      } else if (key === 'd' || key === 'D') {
        clickByLabel('Mode: Subtract');
      }
    };
    doc.addEventListener('keydown', onKey);
  } catch (err) {
    // Cross-origin / sandbox — silent fail. Buttons still work.
  }
})();
</script>
"""


def render_keyboard_shortcuts() -> None:
    """Inject best-effort keyboard shortcut JS.

    Bindings (when the iframe sandbox allows):
      S            — Mode: Replace
      A            — Mode: Add
      D            — Mode: Subtract
      Esc          — Clear selection
      Ctrl/Cmd+Z   — Undo
      Ctrl/Cmd+Shift+Z — Redo

    Pan/Zoom/Reset View are not bound here because they live in Plotly's
    modebar; users can press its built-in shortcuts. If Streamlit's
    cross-origin policy blocks ``window.parent.document`` access, the
    visible buttons remain the primary control surface.
    """
    import streamlit.components.v1 as components
    components.html(_KEYBOARD_JS, height=0)
