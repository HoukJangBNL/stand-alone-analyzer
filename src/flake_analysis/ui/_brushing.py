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
INTERACTION_ZOOM = "zoom"
ALL_INTERACTION_MODES = (INTERACTION_SINGLE, INTERACTION_LASSO, INTERACTION_ZOOM)


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

    Switching is a metadata change only — selected_ids and history are
    preserved. Unknown values fall back to single.

    For ``"single"`` we additionally snap the lasso sub-mode back to
    ``MODE_REPLACE`` defensively. The sub-mode is irrelevant in
    single-pick (every click replaces), but resetting it means a
    subsequent ``L`` press lands in Replace rather than reviving a
    stale Add/Subtract context. We also clear stale Plotly chart
    selection event payloads parked in ``st.session_state`` under the
    selector/clustering pane key prefixes so Streamlit does not replay
    a previous lasso event when the figure rebuilds with a new
    ``dragmode``.
    """
    if mode not in ALL_INTERACTION_MODES:
        mode = INTERACTION_SINGLE
    state.interaction_mode = mode
    if mode == INTERACTION_SINGLE:
        state.mode = MODE_REPLACE
    _purge_pane_event_state()


def _purge_pane_event_state() -> None:
    """Drop cached Plotly chart event payloads on interaction-mode change.

    Streamlit's ``plotly_chart`` with ``on_select="rerun"`` parks the
    most recent event under a session_state key derived from the
    chart's ``key=``. When ``dragmode`` flips between ``pan`` and
    ``lasso`` we want a clean slate so a stale lasso polygon can not
    be re-applied to the freshly-rebuilt figure. Operates only on
    keys whose prefix matches the panes registered by Selector and
    Clustering tabs (``sel_pane_*`` / ``clu_pane_*``); everything
    else (filter widgets, seed groups, …) is left untouched.
    """
    try:
        for key in list(st.session_state.keys()):
            if isinstance(key, str) and (
                key.startswith("sel_pane_") or key.startswith("clu_pane_")
            ):
                del st.session_state[key]
    except Exception:  # pragma: no cover - defensive
        # In test harnesses session_state may be a dict-like mock that
        # doesn't support iteration; degrade gracefully rather than
        # taking the whole rerun down.
        pass


def get_dragmode(state: BrushingState) -> str:
    """Return the Plotly ``dragmode`` for the current interaction mode.

    * ``"pan"`` for single-pick (left-drag pans, left-click selects 1 pt)
    * ``"lasso"`` for lasso mode (left-drag draws lasso)
    * ``"zoom"`` for zoom mode (left-drag draws box → zoom in)
    """
    if state.interaction_mode == INTERACTION_LASSO:
        return "lasso"
    if state.interaction_mode == INTERACTION_ZOOM:
        return "zoom"
    return "pan"


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
    # DEBUG: trace what Plotly actually returns. Logged to streamlit's
    # terminal so we can correlate "looks like everything got selected"
    # with the actual id count + sample of returned ids.
    import sys as _sys
    if ids is None:
        print("[DEBUG handle_selection_event] no selection (ids is None)", file=_sys.stderr, flush=True)
    elif not ids:
        print("[DEBUG handle_selection_event] empty selection (0 ids)", file=_sys.stderr, flush=True)
    else:
        sample = sorted(list(ids))[:5]
        print(
            f"[DEBUG handle_selection_event] mode={state.mode} "
            f"prior_selected={len(state.selected_ids)} "
            f"lasso_returned={len(ids)} "
            f"sample={sample}",
            file=_sys.stderr,
            flush=True,
        )
    if ids is None:
        return False
    if not ids:
        return False
    apply_lasso(state, ids)
    print(
        f"[DEBUG handle_selection_event] AFTER apply_lasso "
        f"new_selected_count={len(state.selected_ids)}",
        file=_sys.stderr,
        flush=True,
    )
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
_BTN_LASSO_REPLACE = "Lasso: Replace"
_BTN_LASSO_ADD = "Lasso: Add (A)"
_BTN_LASSO_SUBTRACT = "Lasso: Subtract (D)"
_BTN_ZOOM = "Zoom (Z)"
_BTN_UNDO = "Undo"
_BTN_REDO = "Redo"
_BTN_CLEAR = "Clear"


def _is_active_single(state: BrushingState) -> bool:
    return state.interaction_mode == INTERACTION_SINGLE


def _is_active_lasso(state: BrushingState, sub_mode: str) -> bool:
    return state.interaction_mode == INTERACTION_LASSO and state.mode == sub_mode


def _is_active_zoom(state: BrushingState) -> bool:
    return state.interaction_mode == INTERACTION_ZOOM


def render_mode_controls(state: BrushingState, key_prefix: str) -> None:
    """Render interaction-mode buttons + Undo/Redo/Clear + status caption.

    Top row: Single-pick / Lasso: Replace / Lasso: Add / Lasso: Subtract.
    Bottom row: Undo / Redo / Clear / status caption.

    The buttons use stable ASCII labels (see ``_BTN_*``) so the keyboard
    shortcut JS (``render_keyboard_shortcuts``) can match them by
    ``innerText`` reliably.
    """
    # Row 1: interaction-mode buttons.
    cols = st.columns([1, 1, 1, 1, 1, 2])
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
            help="Lasso drag replaces selection (L)",
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
        if st.button(
            _BTN_ZOOM,
            key=f"{key_prefix}_mode_zoom",
            type="primary" if _is_active_zoom(state) else "secondary",
            help="Box drag zooms in (Z)",
        ):
            set_interaction_mode(state, INTERACTION_ZOOM)
            st.rerun()
    with cols[5]:
        if _is_active_single(state):
            active_label = "Single-pick"
        elif _is_active_zoom(state):
            active_label = "Zoom"
        else:
            active_label = f"Lasso · {state.mode}"
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
    n_sel = int(is_selected.sum())

    # Two traces — base + overlay — so the overlay stacks visually on top
    # and we can use a non-WebGL Scatter for the overlay (Scattergl ignores
    # marker.line in many builds, which made selection rings invisible).
    base = go.Scattergl(
        x=x,
        y=y,
        mode="markers",
        marker=dict(size=4, color=base_colors, opacity=0.55),
        customdata=ids,
        hovertemplate=(
            f"id=%{{customdata}}<br>{x_label}=%{{x:.3f}}<br>"
            f"{y_label}=%{{y:.3f}}<extra></extra>"
        ),
        name="all",
        showlegend=False,
    )

    traces = [base]
    if n_sel > 0:
        # Build the overlay: prominent orange ring + filled gold core,
        # rendered on top of the base. SVG Scatter (not Scattergl) so the
        # marker line actually renders.
        # IMPORTANT: the overlay must NOT participate in lasso/box selection.
        # If it did, every selected point would re-appear inside any lasso
        # that even brushed the overlay, so a Replace lasso could not
        # actually shrink the selection — the user saw "all flakes
        # re-activated". Setting selectedpoints=[] hard-disables the
        # overlay's selection participation; hoverinfo="skip" stops the
        # overlay from claiming hover events from base markers.
        sel_x = np.asarray(x)[is_selected]
        sel_y = np.asarray(y)[is_selected]
        overlay = go.Scatter(
            x=sel_x,
            y=sel_y,
            mode="markers",
            marker=dict(
                size=12,
                color="#FFC800",  # gold fill, matches Explorer "selected" cue
                line=dict(width=2.5, color="#ff5722"),  # bold orange ring
                opacity=1.0,
            ),
            hoverinfo="skip",
            selectedpoints=[],
            name="selected",
            showlegend=False,
        )
        traces.append(overlay)

    fig = go.Figure(data=traces)
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
    n_sel = int(is_selected.sum())

    base = go.Scatter3d(
        x=rgb[:, 0],
        y=rgb[:, 1],
        z=rgb[:, 2],
        mode="markers",
        marker=dict(size=3, color=base_colors, opacity=0.55),
        customdata=ids,
        hovertemplate=(
            "domain_id=%{customdata}<br>"
            "R=%{x:.3f}, G=%{y:.3f}, B=%{z:.3f}<extra></extra>"
        ),
        name="all",
        showlegend=False,
    )
    traces = [base]
    if n_sel > 0:
        sel_rgb = rgb[is_selected]
        sel_ids = np.asarray(ids)[is_selected]
        overlay = go.Scatter3d(
            x=sel_rgb[:, 0],
            y=sel_rgb[:, 1],
            z=sel_rgb[:, 2],
            mode="markers",
            marker=dict(
                size=8,
                color="#FFC800",
                line=dict(width=1.5, color="#ff5722"),
                opacity=1.0,
            ),
            hoverinfo="skip",
            name="selected",
            showlegend=False,
        )
        traces.append(overlay)

    fig = go.Figure(data=traces)
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
            width="stretch",
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
                width="stretch",
                key=key,
            )
        except (TypeError, ValueError):
            return st.plotly_chart(
                fig,
                config=SHARED_PLOTLY_CONFIG,
                on_select="rerun",
                selection_mode=("box", "lasso"),
                width="stretch",
                key=key,
            )

    if interaction_mode == INTERACTION_ZOOM:
        # Zoom mode: no selection events. Plotly draws a zoom box from the
        # left-drag and applies it as the new viewport. Modebar reset
        # (autoscale) restores full view.
        return st.plotly_chart(
            fig,
            config=SHARED_PLOTLY_CONFIG,
            width="stretch",
            key=key,
        )

    # Lasso mode — restrict to lasso only so the box drag is dedicated to
    # the Zoom mode (a separate interaction_mode) instead of being a
    # second way to brush.
    return st.plotly_chart(
        fig,
        config=SHARED_PLOTLY_CONFIG,
        on_select="rerun",
        selection_mode=("lasso",),
        width="stretch",
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
      // '?' (Shift+/) → open the keyboard shortcuts cheat-sheet.
      // Browsers report key === '?' when shift is held with '/'; we
      // also tolerate the bare '/' fallback since some keyboard layouts
      // surface only that variant.
      if (key === '?' || (e.shiftKey && key === '/')) {
        if (clickByLabel('⌨ Shortcuts (?)')) { e.preventDefault(); }
        return;
      }
      // Plain letter shortcuts (no modifier)
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      var lower = key.toLowerCase();
      if (lower === 's') {
        clickByLabel('Single-pick (S)');
      } else if (lower === 'l') {
        // L → enter lasso mode (defaults to Replace sub-mode);
        // also acts as "back to Replace" when already in lasso.
        clickByLabel('Lasso: Replace');
      } else if (lower === 'a') {
        clickByLabel('Lasso: Add (A)');
      } else if (lower === 'd') {
        clickByLabel('Lasso: Subtract (D)');
      } else if (lower === 'z') {
        // Z → enter zoom mode (left-drag draws a zoom box).
        clickByLabel('Zoom (Z)');
      } else if (lower === 'b') {
        // B → toggle boundary overlay in the image preview. The
        // button's label flips between "Boundary on (B)" and
        // "Boundary off (B)" so we try both.
        if (!clickByLabel('Boundary on (B)')) {
          clickByLabel('Boundary off (B)');
        }
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
    ``L``                        Lasso mode (defaults to Replace; press again to reset to Replace)
    ``A``                        Lasso sub-mode: Add
    ``D``                        Lasso sub-mode: Delete/Subtract
    ``Z``                        Zoom mode (left-drag draws zoom box)
    ``B``                        Toggle image-preview boundary overlay
    ``?``                        Open the keyboard shortcuts cheat-sheet
    ``Esc``                      Clear selection
    ``Ctrl/Cmd+Z``               Undo
    ``Ctrl/Cmd+Shift+Z``         Redo
    ===========================  =======================================

    Pan/Zoom/Reset View are not bound here because they live in Plotly's
    modebar; the modebar buttons (``+``, ``-``, ``Reset View``) handle
    those natively. If Streamlit's cross-origin policy blocks
    ``window.parent.document`` access, the visible buttons remain the
    primary control surface.
    """
    # No-op: the JS injection used st.components.v1.html, which Streamlit
    # is deprecating after 2026-06-01 and which was already blocked by
    # iframe cross-origin sandboxing in many Streamlit deployments. The
    # visible mode buttons remain the canonical control surface; the
    # cheat-sheet dialog (`?` button) lists the shortcuts the buttons
    # accept. Keeping this as a stable no-op so existing call sites still
    # work without raising deprecation warnings on every render.
    return None


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


# ─── Keyboard shortcuts cheat-sheet (Task 3) ───────────────────────────

_SHORTCUTS_BUTTON_LABEL = "⌨ Shortcuts (?)"

# Markdown body kept in one place so the dialog and the expander fallback
# render identical content.
_SHORTCUTS_MARKDOWN = """
**Mode**

- `S` — Single-pick mode (left-click selects one point)
- `L` — Lasso mode (Replace)
- `A` — Lasso: Add to current selection
- `D` — Lasso: Subtract from current selection
- `Z` — Zoom mode (left-drag draws a zoom box)

**Selection**

- `Esc` — Clear current selection
- `Ctrl/⌘ + Z` — Undo
- `Ctrl/⌘ + Shift + Z` — Redo

**Image preview** (Selector tab)

- `B` — Toggle segmentation boundary overlay
- Mouse wheel — Zoom in / out
- Click + drag — Pan
- Reset View — modebar button restores the fit

**Help**

- `?` — Open this cheat-sheet
"""


def _render_shortcuts_body() -> None:
    st.markdown(_SHORTCUTS_MARKDOWN)


# Streamlit ≥1.35 ships ``st.dialog`` as a decorator. On older builds we
# fall back to an inline expander so the cheat-sheet remains accessible.
_HAS_DIALOG = hasattr(st, "dialog")

if _HAS_DIALOG:
    @st.dialog("Keyboard shortcuts")  # type: ignore[misc]
    def _show_shortcuts_dialog() -> None:
        _render_shortcuts_body()
else:  # pragma: no cover - fallback path on legacy Streamlit
    def _show_shortcuts_dialog() -> None:
        with st.expander("⌨ Keyboard shortcuts", expanded=True):
            _render_shortcuts_body()


def render_help_button(key: str = "help_shortcuts_btn") -> None:
    """Render the "Shortcuts (?)" button + open the dialog on click.

    Use a unique ``key`` per tab so Streamlit doesn't complain about
    duplicate widget ids when more than one tab calls this. The button
    label is stable ASCII (with a single keyboard glyph) so the JS
    keyboard handler can match it by ``innerText`` for the ``?``
    shortcut.
    """
    if st.button(
        _SHORTCUTS_BUTTON_LABEL,
        key=key,
        help="Show keyboard shortcuts (?)",
    ):
        _show_shortcuts_dialog()


def render_wheel_capture() -> None:
    """Inject best-effort wheel-event capture for Plotly charts.

    When the cursor is over a Plotly chart, the page's default scroll is
    suppressed so that Plotly's ``scrollZoom`` is not fighting the
    surrounding page scroll. Subject to the same iframe sandbox caveat as
    :func:`render_keyboard_shortcuts`.
    """
    # No-op: see render_keyboard_shortcuts. Same iframe-sandbox + deprecation
    # rationale. Plotly's own scrollZoom still works inside the chart; the
    # difference is just that the page itself may also scroll, which is the
    # legacy default browser behavior.
    return None
