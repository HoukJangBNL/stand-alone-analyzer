# src/flake_analysis/api/schemas/selector.py
"""Selector schemas per backend design §1.2 + §1.3."""
from __future__ import annotations
from typing import Any
from pydantic import BaseModel


class SelectorParams(BaseModel):
    """Mirrors pipeline/selector.py:29-43 — all bounds optional, None = unbounded."""
    area_min: float | None = None
    area_max: float | None = None
    std_r_min: float | None = None
    std_r_max: float | None = None
    std_g_min: float | None = None
    std_g_max: float | None = None
    std_b_min: float | None = None
    std_b_max: float | None = None
    sam2_min: float | None = None
    sam2_max: float | None = None


class SelectorSummary(BaseModel):
    """Result wrapper used inside SSE 'done' event for POST /run/selector."""
    output_path: str
    selected_count: int
    total_count: int
    params: dict[str, Any]
    params_hash: str | None


class SelectorCommitRequest(BaseModel):
    """Body for POST /selector/commit (synchronous JSON)."""
    params: SelectorParams
    lasso_ids: list[int] | None = None


class SelectorCommitSummary(BaseModel):
    """Result of POST /selector/commit — includes intersection stats."""
    output_path: str
    n_committed: int          # final selected=True count after brush ∩ filter
    n_filter_accepted: int    # filter pass count (== selected_count from pipeline)
    n_lasso: int              # |lasso_ids| or 0 if None
    total_count: int
    params_hash: str | None


# Ports tab_selector.py:92-98 to a structured const so the frontend can fetch defaults.
METRIC_DEFS: list[dict[str, Any]] = [
    {"key": "area",  "label": "Area (px)",   "lo": 0.0, "hi": 1_000_000.0, "step": 10.0,  "fmt": "%.0f"},
    {"key": "std_r", "label": "Std R %",     "lo": 0.0, "hi": 100.0,       "step": 0.5,   "fmt": "%.2f"},
    {"key": "std_g", "label": "Std G %",     "lo": 0.0, "hi": 100.0,       "step": 0.5,   "fmt": "%.2f"},
    {"key": "std_b", "label": "Std B %",     "lo": 0.0, "hi": 100.0,       "step": 0.5,   "fmt": "%.2f"},
    {"key": "sam2",  "label": "SAM2 score",  "lo": 0.0, "hi": 1.0,         "step": 0.05,  "fmt": "%.2f"},
]
