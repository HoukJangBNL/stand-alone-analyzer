"""Thin wrapper for manual seed-group GMM clustering.

Wraps ``flake_analysis.core.clustering.engine.InteractiveClusteringEngine`` with file
I/O — loads the per-domain stats NPZ + selector parquet, narrows to the
selected domains, fits a seed-initialized GMM, and persists the result as:

  * ``labels.json``        — frozen interop schema (plan v1 r7 §7.1)
  * ``assignments.parquet`` — per-domain cluster assignment + max posterior
  * ``gmm_model.pkl``       — pickled ``InteractiveClusterResult``
                              (means, covariances, weights, thresholds)

Plan v1 r6 D6.1: ``random_state=42`` is hard-baked in
``InteractiveClusteringEngine.fit``.

Plan v1 r6 D6.2 (positional-index adapter):
    The caller passes ``seed_groups`` keyed by ``domain_id`` (the
    user-facing identifier). The engine, however, indexes ``repr_rgbs`` by
    *positional* row index in the selector-narrowed array. This wrapper
    converts ``domain_ids`` -> positional indices before calling
    ``engine.fit``. Domain ids missing from the selected subset are
    counted in ``n_dropped_seed_ids`` and skipped with a warning rather
    than aborting the run.

Plan v1 r7:
    * Diagnostic counters ``n_dropped_seed_ids`` and
      ``n_dropped_selected_ids`` are returned in the summary dict so the
      caller can detect mapping fallout.
    * ``labels.json`` schema frozen per plan §7.1 (interop contract).
"""
from __future__ import annotations

import hashlib
import json
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from flake_analysis.core._compat import ProgressCallback, msg
from flake_analysis.core.clustering.engine import (
    InteractiveClusterResult,
    InteractiveClusteringEngine,
)


def _hash_params(params: Dict[str, Any]) -> str:
    payload = json.dumps(
        params, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _build_positional_seed_groups(
    seed_groups: Sequence[Dict[str, Any]],
    selected_domain_ids: np.ndarray,
) -> Tuple[List[Dict[str, Any]], int]:
    """Convert domain_id-based seed groups to positional indices.

    ``selected_domain_ids`` is the array (already filtered to ``selected==True``)
    in the same order as the rows fed to the engine. We map each
    ``domain_id`` -> its positional row.

    Returns
    -------
    (positional_groups, n_dropped_seed_ids)
        ``n_dropped_seed_ids`` is the total count of seed-group
        ``domain_id``s that were not present in the selected subset
        (across all groups).
    """
    id_to_pos = {int(did): int(pos) for pos, did in enumerate(selected_domain_ids)}

    out: List[Dict[str, Any]] = []
    n_dropped_seed_ids = 0
    for grp in seed_groups:
        name = grp.get("name", f"group_{len(out)}")
        domain_ids = grp.get("domain_ids", grp.get("indices", []))
        positions: List[int] = []
        for did in domain_ids:
            pos = id_to_pos.get(int(did))
            if pos is None:
                n_dropped_seed_ids += 1
                msg.warning(
                    f"[run_clustering] domain_id {int(did)} in seed group "
                    f"'{name}' is not in selector — dropped"
                )
            else:
                positions.append(pos)
        if not positions:
            raise ValueError(
                f"seed group '{name}' has no valid domain_ids in the "
                f"selected subset"
            )
        out.append({"name": name, "indices": positions, "domain_ids": list(domain_ids)})
    return out, n_dropped_seed_ids


def run_clustering(
    stats_npz_path: Union[str, Path],
    selection_parquet_path: Union[str, Path],
    seed_groups: Sequence[Dict[str, Any]],
    *,
    output_dir: Union[str, Path],
    rgb_threshold: float = 0.5,
    max_iter: int = 100,
    tol: float = 1e-4,
    fit_scope: str = "seeds",
    max_mahalanobis: float = 3.0,
    reg_covar: float = 1.0,
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """Fit GMM with manual seed groups and persist labels + model.

    Parameters
    ----------
    stats_npz_path : str | Path
        NPZ produced by ``run_domain_stats`` (must contain
        ``repr_rgbs`` and ``flake_ids``).
    selection_parquet_path : str | Path
        Parquet produced by ``run_selector`` (columns:
        ``domain_id``, ``selected``).
    seed_groups : sequence of dict
        Each entry must have:
          * ``name`` (str, optional — auto-named when absent),
          * ``domain_ids`` (list[int]) — domain ids assigned by user.
        ``indices`` is accepted as an alias for ``domain_ids`` for
        backward compatibility with the engine's pre-extraction API.
    output_dir : str | Path
        Directory to receive ``labels.json``, ``assignments.parquet``,
        ``gmm_model.pkl``.
    rgb_threshold : float, optional
        Posterior probability cutoff for Filter 1, broadcast to all
        clusters. Default ``0.5``.
    max_iter, tol : float
        EM hyperparameters forwarded to ``GaussianMixture``.

    Returns
    -------
    dict
        Summary including output paths, ``n_clusters``, ``n_assigned``,
        ``n_unassigned``, ``params``, ``params_hash``.
    """
    stats_npz_path = Path(stats_npz_path)
    selection_parquet_path = Path(selection_parquet_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not seed_groups:
        raise ValueError("seed_groups must be non-empty")

    msg.info(
        f"[pipeline.clustering] start npz={stats_npz_path} "
        f"selection={selection_parquet_path} n_groups={len(seed_groups)} "
        f"random_state=42"
    )

    if progress_callback is not None:
        progress_callback(0.1, "Loading stats NPZ...")

    # --- Load stats + selection ------------------------------------------
    npz = np.load(stats_npz_path, allow_pickle=False)
    if "repr_rgbs" not in npz.files or "flake_ids" not in npz.files:
        raise KeyError(
            f"stats NPZ missing required keys (have: {npz.files})"
        )

    repr_rgbs_all = npz["repr_rgbs"]
    flake_ids_all = npz["flake_ids"].astype(np.int64)

    selection_df = pd.read_parquet(selection_parquet_path)
    if not {"domain_id", "selected"}.issubset(selection_df.columns):
        raise KeyError(
            f"selection parquet missing required columns "
            f"(have: {list(selection_df.columns)})"
        )

    # Build domain_id -> position-in-NPZ map, then narrow to selected.
    id_to_npz_pos = {int(did): pos for pos, did in enumerate(flake_ids_all)}
    npz_id_set = set(id_to_npz_pos.keys())
    selected_mask = selection_df["selected"].astype(bool).to_numpy()
    selected_domain_ids = selection_df.loc[selected_mask, "domain_id"].astype(int).to_numpy()

    if selected_domain_ids.size == 0:
        raise ValueError("selection parquet contains zero selected domains")

    # Diagnostic: count selected domain_ids missing from the stats NPZ.
    n_dropped_selected_ids = 0
    npz_positions_list: List[int] = []
    kept_selected_ids: List[int] = []
    for did in selected_domain_ids:
        did_int = int(did)
        if did_int in npz_id_set:
            npz_positions_list.append(id_to_npz_pos[did_int])
            kept_selected_ids.append(did_int)
        else:
            n_dropped_selected_ids += 1
            msg.warning(
                f"[run_clustering] selected domain_id {did_int} not in stats NPZ — dropped"
            )

    npz_positions = np.array(npz_positions_list, dtype=np.int64)
    selected_domain_ids = np.array(kept_selected_ids, dtype=np.int64)

    if selected_domain_ids.size == 0:
        raise ValueError(
            "all selected domain_ids missing from stats NPZ; nothing to cluster"
        )

    repr_rgbs_sel = repr_rgbs_all[npz_positions]
    msg.info(
        f"[pipeline.clustering] selected subset: {repr_rgbs_sel.shape[0]} domains"
    )

    if progress_callback is not None:
        progress_callback(0.3, "Building seed-group positional indices...")

    # --- Positional-index adapter (D6.2) ---------------------------------
    positional_groups, n_dropped_seed_ids = _build_positional_seed_groups(
        seed_groups, selected_domain_ids
    )
    seed_indices_only = [grp["indices"] for grp in positional_groups]

    if progress_callback is not None:
        progress_callback(0.5, f"Fitting GMM (max_iter={max_iter})...")

    # --- Fit GMM ---------------------------------------------------------
    engine = InteractiveClusteringEngine()
    result: InteractiveClusterResult = engine.fit(
        repr_rgbs_sel,
        seed_indices_only,
        rgb_threshold=rgb_threshold,
        max_iter=max_iter,
        tol=tol,
        fit_scope=fit_scope,
        max_mahalanobis=max_mahalanobis,
        reg_covar=reg_covar,
    )

    if progress_callback is not None:
        progress_callback(0.8, "Computing posteriors...")

    # --- Persist outputs -------------------------------------------------
    assignments_data: Dict[str, Any] = {
        "domain_id": selected_domain_ids.astype(np.int64),
        "cluster_label": result.labels.astype(np.int64),
        "max_posterior": result.probabilities.astype(np.float64),
    }
    # Nearest-cluster Mahalanobis distance — feeds the live distance
    # gate slider in the UI so the user can re-filter without re-fit.
    if result.nearest_mahalanobis is not None:
        assignments_data["nearest_mahalanobis"] = (
            result.nearest_mahalanobis.astype(np.float64)
        )
    assignments_df = pd.DataFrame(assignments_data)
    assignments_path = output_dir / "assignments.parquet"
    assignments_df.to_parquet(assignments_path, engine="pyarrow", index=False)

    if progress_callback is not None:
        progress_callback(0.95, "Writing labels.json + assignments.parquet...")

    n_assigned = int((result.labels >= 0).sum())
    n_unassigned = int((result.labels == -1).sum())

    # --- labels.json (frozen schema per plan v1 r7 §7.1) -----------------
    # Mean RGB per cluster, computed on the assigned (label >= 0) subset.
    cluster_centers = result.cluster_centers
    thresholds_list = (
        list(result.thresholds)
        if result.thresholds is not None
        else [float(rgb_threshold)] * int(result.n_clusters)
    )
    groups_payload: List[Dict[str, Any]] = []
    for cid in range(int(result.n_clusters)):
        member_mask = result.labels == cid
        size = int(member_mask.sum())
        center = cluster_centers[cid]
        groups_payload.append(
            {
                "id": cid,
                "name": positional_groups[cid]["name"]
                if cid < len(positional_groups)
                else f"group_{cid}",
                "size": size,
                "mean_rgb": [float(center[0]), float(center[1]), float(center[2])],
            }
        )

    # assignments: keys are domain_id (string), values are cluster label.
    # Unassigned (-1) entries are excluded — noise_label captures the sentinel.
    assignments_payload: Dict[str, int] = {
        str(int(did)): int(lab)
        for did, lab in zip(selected_domain_ids, result.labels)
        if int(lab) >= 0
    }

    thresholds_payload: Dict[str, float] = {
        str(cid): float(thresholds_list[cid]) for cid in range(int(result.n_clusters))
    }

    labels_payload = {
        "version": 1,
        "n_clusters": int(result.n_clusters),
        "groups": groups_payload,
        "assignments": assignments_payload,
        "thresholds": thresholds_payload,
        "noise_label": -1,
        "random_state": 42,
        "fitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    labels_path = output_dir / "labels.json"
    labels_path.write_text(json.dumps(labels_payload, indent=2))

    gmm_path = output_dir / "gmm_model.pkl"
    with open(gmm_path, "wb") as f:
        pickle.dump(result, f)

    msg.info(
        f"[pipeline.clustering] wrote labels={labels_path.name} "
        f"assignments={assignments_path.name} gmm={gmm_path.name} "
        f"(assigned={n_assigned}, unassigned={n_unassigned}, "
        f"dropped_seed_ids={n_dropped_seed_ids}, "
        f"dropped_selected_ids={n_dropped_selected_ids})"
    )

    if progress_callback is not None:
        progress_callback(1.0, "Done")

    params: Dict[str, Any] = {
        "stats_npz_path": str(stats_npz_path),
        "selection_parquet_path": str(selection_parquet_path),
        "n_groups": len(positional_groups),
        "rgb_threshold": rgb_threshold,
        "max_iter": max_iter,
        "tol": tol,
        "random_state": 42,
        "fit_scope": fit_scope,
        "max_mahalanobis": max_mahalanobis,
        "reg_covar": reg_covar,
    }
    return {
        "labels_path": labels_path,
        "assignments_path": assignments_path,
        "gmm_model_path": gmm_path,
        "n_clusters": int(result.n_clusters),
        "n_assigned": n_assigned,
        "n_unassigned": n_unassigned,
        "n_dropped_seed_ids": n_dropped_seed_ids,
        "n_dropped_selected_ids": n_dropped_selected_ids,
        "params": params,
        "params_hash": _hash_params(params),
    }
