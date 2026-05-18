"""Shared brushing scaffolding for cross-pane scatter selection.

v0.1.3 introduces a two-tier interaction model:

* **Single-pick mode** (default): left-click on a point selects that one
  point only (replacing the prior selection); left-drag *pans* the chart.
* **Lasso mode**: left-drag draws a lasso/box selection. Three sub-modes
  control how new lasso ids combine with the current selection:
  Replace / Add / Subtract.

Mode is held in :class:`BrushingState.interaction_mode`. Plotly's
``dragmode`` is computed from that via :func:`get_dragmode` (``"pan"`` for
single, ``"lasso"`` for lasso).

Public API:
    BrushingState                 — per-tab session_state-backed object
    get_brushing_state(prefix)    — lazy init + return
    set_interaction_mode          — switch between single / lasso
    get_dragmode                  — Plotly dragmode for current state
    push_history / undo / redo    — selection history (bounded deque, 20)
    apply_lasso(state, ids)       — combine new lasso ids per current sub-mode
    handle_click_event(event, st) — single-pick: replace selection w/ one id
    handle_selection_event(event, state) — lasso: extract customdata ids
    render_mode_controls(state, prefix)  — buttons row (Single / Lasso R/A/D)
    render_undo_redo_clear(state, prefix) — Undo / Redo / Clear row
    make_2d_scatter(...) / make_3d_scatter(...)
    SHARED_PLOTLY_CONFIG          — scrollZoom + display tweaks
    render_scatter(fig, key, ...) — st.plotly_chart wrapper
    render_keyboard_shortcuts()   — best-effort JS keymap
    render_wheel_capture()        — best-effort wheel-over-plotly capture

Modes:
    MODE_REPLACE — next lasso replaces selected_ids
    MODE_ADD     — next lasso union with selected_ids
    MODE_SUBTRACT — next lasso removed from selected_ids

Per plan: keyboard shortcuts and wheel capture are best-effort. Streamlit
renders the app inside an iframe, and ``window.parent.document`` access
may be blocked by cross-origin sandboxing. The visible mode buttons remain
the primary control surface.
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

INTERACTION_SINGLE = "single"
INTERACTION_LASSO = "lasso"
ALL_INTERACTION_MODES = (INTERACTION_SINGLE, INTERACTION_LASSO)


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
    """Per-tab brushing state (selection + history + mode + interaction).

    Stored under a single session_state slot keyed ``f"{prefix}.brushing"``.
    The ``history`` and ``redo_stack`` are bounded to ``HISTORY_MAX`` entries.

    Attributes:
        selected_ids: Currently brushed/selected domain ids.
        history: Bounded deque of prior selections (for undo).
        redo_stack: Stack of undone selections (for redo).
        mode: Lasso sub-mode (``replace`` / ``add`` / ``subtract``). Only
            meaningful when ``interaction_mode == "lasso"``; in single-pick
            mode every click replaces the selection.
        interaction_mode: ``"single"`` (left-click → 1 point, drag → pan)
            or ``"lasso"`` (drag → lasso/box select).
        focus_id: Domain id chosen by an explicit row-click in the flake
            list. When set, the image preview prefers this over the
            min(selected_ids) fallback. Cleared by mode switches that
            wipe the selection (currently kept across mode switches; row
            click is a separate "focus" concept).
    """

    selected_ids: Set[int] = field(default_factory=set)
    history: Deque[Set[int]] = field(default_factory=lambda: deque(maxlen=HISTORY_MAX))
    redo_stack: List[Set[int]] = field(default_factory=list)
    mode: str = MODE_REPLACE
    interaction_mode: str = INTERACTION_SINGLE
    focus_id: Optional[int] = None


def _state_key(prefix: str) -> str:
    return f"{prefix}.brushing"


def get_brushing_state(key_prefix: str) -> BrushingState:
    """Lazy-init session_state-backed BrushingState for a tab."""
    key = _state_key(key_prefix)
    state = st.session_state.get(key)
    if state is None:
        state = BrushingState()
        st.session_state[key] = state
    # Backward-compat: older sessions may have a state dataclass missing
    # the new fields. Patch them in defensively.
    if not hasattr(state, "interaction_mode"):
        state.interaction_mode = INTERACTION_SINGLE
    if not hasattr(state, "focus_id"):
        state.focus_id = None
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


# ─── Interaction-mode helpers ────────────────────────────────────────────

def set_interaction_mode(state: BrushingState, mode: str) -> None:
    """Switch between ``"single"`` and ``"lasso"``.

    Switching is a metadata change only — selected_ids, history, and the
    lasso sub-mode are preserved. Unknown values fall back to single.
    """
    if mode not in ALL_INTERACTION_MODES:
        mode = INTERACTION_SINGLE
    state.interaction_mode = mode


def get_dragmode(state: BrushingState) -> str:
    """Return the Plotly ``dragmode`` for the current interaction mode.

    * ``"pan"`` for single-pick (left-drag pans, left-click selects 1 pt)
    * ``"lasso"`` for lasso mode (left-drag draws lasso)
    """
    return "lasso" if state.interaction_mode == INTERACTION_LASSO else "pan"


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
    """Apply a Plotly **lasso/box** selection event to ``state``.

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


def handle_click_event(event: Any, state: BrushingState) -> bool:
    """Apply a single-pick click event to ``state`` (replace with 1 id).

    Streamlit's plotly_chart event in ``selection_mode=("points",)`` (when
    supported) yields the same event shape as lasso, but with a single
    customdata entry. We robustly take the first id and replace the
    selection (irrespective of state.mode — single-pick is always replace).

    Returns True if state was modified.
    """
    ids = _extract_ids_from_event(event)
    if not ids:
        return False
    # Single-pick: deterministically pick the smallest id when multiple
    # come back (e.g. overlapping markers). This matches the
    # _focus_domain_id min() convention so the preview tracks the click.
    pick = min(ids)
    push_history(state)
    state.selected_ids = {pick}
    return True


# ─── Mode controls ───────────────────────────────────────────────────────

# Button labels — kept ASCII so the keyboard-shortcut JS innerText match
# stays robust. (Emoji glyphs proved brittle across Streamlit versions.)
_BTN_SINGLE = "Single-pick (S)"
_BTN_LASSO_REPLACE = "Lasso: Replace (L)"
_BTN_LASSO_ADD = "Lasso: Add (A)"
_BTN_LASSO_SUBTRACT = "Lasso: Subtract (D)"
_BTN_UNDO = "Undo"
_BTN_REDO = "Redo"
_BTN_CLEAR = "Clear"


def _is_active_single(state: BrushingState) -> bool:
    return state.interaction_mode == INTERACTION_SINGLE


def _is_active_lasso(state: BrushingState, sub_mode: str) -> bool:
    return state.interaction_mode == INTERACTION_LASSO and state.mode == sub_mode


def render_mode_controls(state: BrushingState, key_prefix: str) -> None:
    """Render interaction-mode buttons + Undo/Redo/Clear + status caption.

    Top row: Single-pick / Lasso: Replace / Lasso: Add / Lasso: Subtract.
    Bottom row: Undo / Redo / Clear / status caption.

    The buttons use stable ASCII labels (see ``_BTN_*``) so the keyboard
    shortcut JS (``render_keyboard_shortcuts``) can match them by
    ``innerText`` reliably.
    """
    # Row 1: interaction-mode buttons.
    cols = st.columns([1, 1, 1, 1, 2])
    with cols[0]:
        if st.button(
            _BTN_SINGLE,
            key=f"{key_prefix}_mode_single",
            type="primary" if _is_active_single(state) else "secondary",
            help="Left-click selects one point; left-drag pans (S)",
        ):
            set_interaction_mode(state, INTERACTION_SINGLE)
            st.rerun()
    with cols[1]:
        if st.button(
            _BTN_LASSO_REPLACE,
            key=f"{key_prefix}_mode_lasso_replace",
            type="primary" if _is_active_lasso(state, MODE_REPLACE) else "secondary",
            help="Lasso/box drag replaces selection (L or R)",
        ):
            set_interaction_mode(state, INTERACTION_LASSO)
            state.mode = MODE_REPLACE
            st.rerun()
    with cols[2]:
        if st.button(
            _BTN_LASSO_ADD,
            key=f"{key_prefix}_mode_lasso_add",
            type="primary" if _is_active_lasso(state, MODE_ADD) else "secondary",
            help="Lasso adds to current selection (A)",
        ):
            set_interaction_mode(state, INTERACTION_LASSO)
            state.mode = MODE_ADD
            st.rerun()
    with cols[3]:
        if st.button(
            _BTN_LASSO_SUBTRACT,
            key=f"{key_prefix}_mode_lasso_subtract",
            type="primary" if _is_active_lasso(state, MODE_SUBTRACT) else "secondary",
            help="Lasso subtracts from current selection (D)",
        ):
            set_interaction_mode(state, INTERACTION_LASSO)
            state.mode = MODE_SUBTRACT
            st.rerun()
    with cols[4]:
        active_label = (
            "Single-pick"
            if _is_active_single(state)
            else f"Lasso · {state.mode}"
        )
        st.caption(
            f"Mode: **{active_label}** · "
            f"selected={len(state.selected_ids):,} · "
            f"history={len(state.history)} · redo={len(state.redo_stack)}"
        )

    # Row 2: history / clear.
    h_cols = st.columns([1, 1, 1, 4])
    with h_cols[0]:
        if st.button(
            _BTN_UNDO,
            key=f"{key_prefix}_undo",
            help="Undo last selection change (Ctrl/Cmd+Z)",
            disabled=not state.history,
        ):
            undo(state)
            st.rerun()
    with h_cols[1]:
        if st.button(
            _BTN_REDO,
            key=f"{key_prefix}_redo",
            help="Redo (Ctrl/Cmd+Shift+Z)",
            disabled=not state.redo_stack,
        ):
            redo(state)
            st.rerun()
    with h_cols[2]:
        if st.button(
            _BTN_CLEAR,
            key=f"{key_prefix}_clear",
            help="Clear current selection (Esc)",
            disabled=not state.selected_ids,
        ):
            clear_selection(state)
            st.rerun()


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
    dragmode: str = "lasso",
):
    """Build a Plotly Scattergl figure with selection ring on selected ids.

    ``base_colors`` is a per-point color array (e.g. red/green for accept/
    reject, or cluster palette). Points whose id is in ``selected_ids``
    get a slightly larger size + an orange ring.

    ``dragmode`` selects Plotly's drag behavior — pass ``"pan"`` for the
    single-pick interaction mode and ``"lasso"`` for lasso mode.
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
                f"id=%{{customdata}}<br>{x_label}=%{{x:.3f}}<br>"
                f"{y_label}=%{{y:.3f}}<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=10, b=30),
        xaxis_title=x_label,
        yaxis_title=y_label,
        dragmode=dragmode,
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
    """3D RGB scatter (display-only — no lasso event support).

    Hover tooltips show R/G/B to 3 decimal places. domain_id stays as the
    integer customdata.
    """
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
                "R=%{x:.3f}, G=%{y:.3f}, B=%{z:.3f}<extra></extra>"
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

def render_scatter(
    fig,
    key: str,
    *,
    on_select: bool = True,
    interaction_mode: str = INTERACTION_LASSO,
):
    """Render st.plotly_chart with shared config + (optional) selection events.

    When ``on_select`` is True, the call returns the streamlit event object;
    when False, the chart handle. The selection_mode tuple varies with
    ``interaction_mode``:

    * ``"single"`` → ``("points",)`` for click-to-select if Streamlit supports
      it (≥1.30 plotly_chart). On older versions Streamlit raises and we
      fall back to ``("lasso", "box")`` so at least drag still works; the
      caller can also wire ``handle_click_event`` to ``selection.points``.
    * ``"lasso"``  → ``("lasso", "box")`` (the legacy v0.1.2 behavior).
    """
    if not on_select:
        return st.plotly_chart(
            fig,
            config=SHARED_PLOTLY_CONFIG,
            use_container_width=True,
            key=key,
        )

    if interaction_mode == INTERACTION_SINGLE:
        # Streamlit ≥1.30 supports "points" for click selection. Older
        # builds will TypeError; fall back to lasso/box so the chart at
        # least renders. The on_select event still carries point clicks
        # in lasso mode (Plotly fires a single-point selection on click).
        try:
            return st.plotly_chart(
                fig,
                config=SHARED_PLOTLY_CONFIG,
                on_select="rerun",
                selection_mode=("points", "box", "lasso"),
                use_container_width=True,
                key=key,
            )
        except (TypeError, ValueError):
            return st.plotly_chart(
                fig,
                config=SHARED_PLOTLY_CONFIG,
                on_select="rerun",
                selection_mode=("box", "lasso"),
                use_container_width=True,
                key=key,
            )

    # Lasso mode — original behavior.
    return st.plotly_chart(
        fig,
        config=SHARED_PLOTLY_CONFIG,
        on_select="rerun",
        selection_mode=("lasso", "box"),
        use_container_width=True,
        key=key,
    )


# ─── Keyboard shortcuts (best-effort JS injection) ─────────────────────

_KEYBOARD_JS = """
<script>
(function() {
  // Best-effort keyboard shortcuts. Streamlit renders the app inside an
  // iframe; we reach into window.parent.document to find the buttons.
  // Cross-origin sandboxing CAN block this — if so, the buttons remain
  // the primary mechanism.
  try {
    var doc = window.parent.document;
    if (!doc || doc.__brushingShortcutsBoundV2) return;
    doc.__brushingShortcutsBoundV2 = true;

    var clickByLabel = function(label) {
      var btns = doc.querySelectorAll('button');
      for (var i = 0; i < btns.length; i++) {
        var b = btns[i];
        if ((b.innerText || '').trim() === label) { b.click(); return true; }
      }
      return false;
    };

    var hasButton = function(label) {
      var btns = doc.querySelectorAll('button');
      for (var i = 0; i < btns.length; i++) {
        if ((btns[i].innerText || '').trim() === label) return true;
      }
      return false;
    };

    var onKey = function(e) {
      var tag = (e.target && e.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      var key = e.key || '';

      // Ctrl/Cmd+Shift+Z → Redo (must check BEFORE plain Ctrl/Cmd+Z)
      if ((e.ctrlKey || e.metaKey) && key.toLowerCase() === 'z' && e.shiftKey) {
        if (clickByLabel('Redo')) { e.preventDefault(); }
        return;
      }
      // Ctrl/Cmd+Z → Undo
      if ((e.ctrlKey || e.metaKey) && key.toLowerCase() === 'z') {
        if (clickByLabel('Undo')) { e.preventDefault(); }
        return;
      }
      // Esc → Clear
      if (key === 'Escape') {
        clickByLabel('Clear');
        return;
      }
      // Plain letter shortcuts (no modifier)
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      var lower = key.toLowerCase();
      if (lower === 's') {
        clickByLabel('Single-pick (S)');
      } else if (lower === 'l') {
        // L → enter lasso mode (defaults to Replace sub-mode)
        clickByLabel('Lasso: Replace (L)');
      } else if (lower === 'r') {
        // R → set sub-mode Replace (only effective when already in lasso)
        clickByLabel('Lasso: Replace (L)');
      } else if (lower === 'a') {
        clickByLabel('Lasso: Add (A)');
      } else if (lower === 'd') {
        clickByLabel('Lasso: Subtract (D)');
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

    ===========================  =======================================
    Key                          Action
    ===========================  =======================================
    ``S``                        Single-pick mode
    ``L``                        Lasso mode (defaults to Replace)
    ``R``                        Lasso sub-mode: Replace
    ``A``                        Lasso sub-mode: Add
    ``D``                        Lasso sub-mode: Delete/Subtract
    ``Esc``                      Clear selection
    ``Ctrl/Cmd+Z``               Undo
    ``Ctrl/Cmd+Shift+Z``         Redo
    ===========================  =======================================

    Pan/Zoom/Reset View are not bound here because they live in Plotly's
    modebar; users can press its built-in shortcuts. If Streamlit's
    cross-origin policy blocks ``window.parent.document`` access, the
    visible buttons remain the primary control surface.
    """
    import streamlit.components.v1 as components
    components.html(_KEYBOARD_JS, height=0)


# ─── Wheel-zoom capture (best-effort) ───────────────────────────────────

_WHEEL_CAPTURE_JS = """<script>
(function(){
  // Prevent the page from scrolling when the user wheels over a Plotly
  // chart. Plotly's scrollZoom keeps working because we only block the
  // parent document's default; Plotly attaches its own listener earlier.
  // Same iframe-sandbox caveat as keyboard shortcuts.
  var doc = window.parent.document;
  if (!doc || doc.__plotlyWheelCaptureBound) return;
  doc.__plotlyWheelCaptureBound = true;
  try {
    doc.addEventListener('wheel', (e) => {
      var tgt = e.target;
      if (tgt && tgt.closest && tgt.closest('.js-plotly-plot')) {
        e.preventDefault();
      }
    }, { passive: false });
  } catch (err) { /* silent */ }
})();
</script>"""


def render_wheel_capture() -> None:
    """Inject best-effort wheel-event capture for Plotly charts.

    When the cursor is over a Plotly chart, the page's default scroll is
    suppressed so that Plotly's ``scrollZoom`` is not fighting the
    surrounding page scroll. Subject to the same iframe sandbox caveat as
    :func:`render_keyboard_shortcuts`.
    """
    import streamlit.components.v1 as components
    components.html(_WHEEL_CAPTURE_JS, height=0)
