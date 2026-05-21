"""Brush ∩ filter intersection — ports tab_selector.py:773-779."""
from __future__ import annotations
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


def apply_brush_intersection(
    selection_parquet_path: str | Path,
    *,
    lasso_ids: list[int] | None,
) -> int:
    """Tighten ``selected`` to (filter ∩ lasso) when a non-empty lasso set is given.

    Mirrors tab_selector.py:773-779:
      - lasso_ids None or [] → file untouched, return current True count
      - lasso_ids non-empty  → selected := selected & isin(lasso_ids), rewrite parquet

    Returns the final count of selected=True rows.
    """
    p = Path(selection_parquet_path)
    if not p.exists():
        raise FileNotFoundError(f"selection.parquet missing at {p}")

    df = pd.read_parquet(p)
    if lasso_ids is None or len(lasso_ids) == 0:
        return int(df["selected"].astype(bool).sum())

    brush_arr = np.fromiter((int(x) for x in lasso_ids), dtype=np.int64)
    in_brush = df["domain_id"].astype(np.int64).isin(brush_arr)
    df["selected"] = df["selected"].astype(bool) & in_brush
    df.to_parquet(p, index=False)
    return int(df["selected"].sum())
