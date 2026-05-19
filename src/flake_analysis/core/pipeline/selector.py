"""Thin wrapper for selector commit (5-metric bidirectional min/max filter).

Pure CSV/parquet writing — no algorithmic core (the filter is a numpy
boolean mask AND-reduce). Mirrors the contract from Qpress's
``selector_commit_operation.py`` / ``selector_serializer.py``:

  Filter axes (all bidirectional, ``None`` = unbounded):
    * area     — pixel area per domain
    * std_r    — red-channel std as percent of mean
    * std_g    — green-channel std as percent
    * std_b    — blue-channel std as percent
    * sam2     — SAM2 confidence score (optional; absent metric is
                 treated as "no constraint" per ``allow_missing`` semantics)

The input NPZ is the artifact from ``run_domain_stats`` (or any NPZ that
follows the ``repr_rgbs / std_pcts / areas / flake_ids`` schema; ``sam2``
is optional).

Output is a parquet with ``(domain_id, selected: bool)`` rows for *all*
input domains so consumers can index by row order.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Union

import numpy as np
import pandas as pd

from flake_analysis.core._compat import ProgressCallback, msg


def _hash_params(params: Dict[str, Any]) -> str:
    payload = json.dumps(
        params, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _bound_mask(values: np.ndarray, lo: Optional[float], hi: Optional[float]) -> np.ndarray:
    """Return boolean mask for ``lo <= values <= hi`` with None = unbounded."""
    mask = np.ones(values.shape[0], dtype=bool)
    if lo is not None:
        mask &= values >= float(lo)
    if hi is not None:
        mask &= values <= float(hi)
    return mask


def run_selector(
    stats_npz_path: Union[str, Path],
    *,
    output_path: Union[str, Path],
    area_min: Optional[float] = None,
    area_max: Optional[float] = None,
    std_r_min: Optional[float] = None,
    std_r_max: Optional[float] = None,
    std_g_min: Optional[float] = None,
    std_g_max: Optional[float] = None,
    std_b_min: Optional[float] = None,
    std_b_max: Optional[float] = None,
    sam2_min: Optional[float] = None,
    sam2_max: Optional[float] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """Apply the 5-metric bidirectional filter to per-domain stats.

    Parameters
    ----------
    stats_npz_path : str | Path
        NPZ produced by ``run_domain_stats`` (must contain
        ``repr_rgbs``, ``std_pcts``, ``areas``, ``flake_ids``;
        ``sam2`` is optional).
    output_path : str | Path
        Destination parquet with columns ``(domain_id, selected)``.
        Parent directory is created if missing.
    area_min, area_max : float | None
        Bidirectional bounds on the ``areas`` column (pixel count).
    std_r_min, std_r_max : float | None
        Bidirectional bounds on ``std_pcts[:, 0]``.
    std_g_min, std_g_max : float | None
        Bidirectional bounds on ``std_pcts[:, 1]``.
    std_b_min, std_b_max : float | None
        Bidirectional bounds on ``std_pcts[:, 2]``.
    sam2_min, sam2_max : float | None
        Bidirectional bounds on ``sam2``. If the NPZ has no ``sam2`` array,
        these bounds are ignored (``allow_missing=True`` semantics).

    Returns
    -------
    dict
        Summary including ``selected_count``, ``total_count``,
        ``output_path``, ``params``, ``params_hash``.
    """
    stats_npz_path = Path(stats_npz_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    msg.info(
        f"[pipeline.selector] start npz={stats_npz_path} out={output_path}"
    )

    if progress_callback is not None:
        progress_callback(0.0, "Loading stats NPZ...")

    npz = np.load(stats_npz_path, allow_pickle=False)
    required = ("std_pcts", "areas", "flake_ids")
    for key in required:
        if key not in npz.files:
            raise KeyError(
                f"stats NPZ missing required key '{key}' (have: {npz.files})"
            )

    std_pcts = npz["std_pcts"]
    areas = npz["areas"].astype(np.float64)
    flake_ids = npz["flake_ids"].astype(np.int64)
    n = flake_ids.shape[0]

    if std_pcts.shape != (n, 3):
        raise ValueError(
            f"std_pcts shape {std_pcts.shape} incompatible with N={n} "
            f"(expected ({n}, 3))"
        )

    # 5 metric bounds AND-reduced.
    masks = [
        _bound_mask(areas, area_min, area_max),
        _bound_mask(std_pcts[:, 0].astype(np.float64), std_r_min, std_r_max),
        _bound_mask(std_pcts[:, 1].astype(np.float64), std_g_min, std_g_max),
        _bound_mask(std_pcts[:, 2].astype(np.float64), std_b_min, std_b_max),
    ]

    if "sam2" in npz.files:
        sam2 = npz["sam2"].astype(np.float64)
        if sam2.shape[0] != n:
            raise ValueError(
                f"sam2 length {sam2.shape[0]} != flake_ids length {n}"
            )
        masks.append(_bound_mask(sam2, sam2_min, sam2_max))
    else:
        # allow_missing=True semantics — ignore sam2 bounds when the column
        # is absent from the NPZ. Log only when bounds were actually set.
        if sam2_min is not None or sam2_max is not None:
            msg.warning(
                "[pipeline.selector] sam2 bounds requested but stats NPZ "
                "has no 'sam2' column; ignoring (allow_missing=True)"
            )

    if progress_callback is not None:
        progress_callback(0.5, "Applying 5-metric filter...")

    selected = np.logical_and.reduce(masks)

    df = pd.DataFrame(
        {
            "domain_id": flake_ids,
            "selected": selected.astype(bool),
        }
    )
    df.to_parquet(output_path, engine="pyarrow", index=False)

    selected_count = int(selected.sum())
    total_count = int(n)
    msg.info(
        f"[pipeline.selector] selected {selected_count}/{total_count} domains -> {output_path}"
    )

    if progress_callback is not None:
        progress_callback(1.0, "Done")

    params: Dict[str, Any] = {
        "stats_npz_path": str(stats_npz_path),
        "area_min": area_min,
        "area_max": area_max,
        "std_r_min": std_r_min,
        "std_r_max": std_r_max,
        "std_g_min": std_g_min,
        "std_g_max": std_g_max,
        "std_b_min": std_b_min,
        "std_b_max": std_b_max,
        "sam2_min": sam2_min,
        "sam2_max": sam2_max,
    }
    return {
        "output_path": output_path,
        "selected_count": selected_count,
        "total_count": total_count,
        "params": params,
        "params_hash": _hash_params(params),
    }
