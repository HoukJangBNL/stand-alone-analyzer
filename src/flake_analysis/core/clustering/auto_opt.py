"""Auto-optimisation metrics for the seed-driven GMM clustering engine.

Primary: blob-recall (KD-tree k-NN around seeds). Tiebreaker: Mahalanobis
self-distribution margin. Public driver: auto_tune_reg_covar.
See claudedocs/clustering-tunable-spec.md §4.
"""
from __future__ import annotations

from typing import Dict, List, Sequence

import numpy as np
from scipy.spatial import cKDTree


def _seed_neighbours(
    points: np.ndarray,
    seeds: Dict[int, Sequence[int]],
    k: int,
) -> Dict[int, np.ndarray]:
    """For each cluster id, return the (n_seeds, k) array of neighbour row indices.

    The self-column (k=0 nearest = the seed itself) is dropped. Empty seed
    groups are omitted from the returned dict.
    """
    if points.shape[0] == 0:
        return {}
    tree = cKDTree(points)
    out: Dict[int, np.ndarray] = {}
    for cid, seed_idxs in seeds.items():
        seed_idxs = list(seed_idxs)
        if not seed_idxs:
            continue
        _, idx = tree.query(points[seed_idxs], k=min(k + 1, points.shape[0]))
        if idx.ndim == 1:
            idx = idx.reshape(-1, 1)
        out[cid] = idx[:, 1:]  # drop self column
    return out


def _blob_recall_from_neighbours(
    neighbours: Dict[int, np.ndarray],
    labels: np.ndarray,
) -> float:
    """Score blob-recall given precomputed seed neighbour indices."""
    per_cluster_recall: List[float] = []
    for cid, idx in neighbours.items():
        nbr_labels = labels[idx]
        share = (nbr_labels == cid).mean(axis=1)
        per_cluster_recall.append(float(share.mean()))
    if not per_cluster_recall:
        return 0.0
    return float(np.mean(per_cluster_recall))


def compute_blob_recall(
    points: np.ndarray,
    seeds: Dict[int, Sequence[int]],
    labels: np.ndarray,
    k: int = 10,
) -> float:
    """Mean over clusters of (mean over seeds of (k-NN share-of-cluster-label)).

    For each cluster's seed members, query their k nearest neighbours in
    ``points``; recall is the fraction of those neighbours whose label
    equals the cluster id. Returns the cross-cluster mean. Empty seed
    groups skipped; if every group is empty returns 0.0.
    """
    neighbours = _seed_neighbours(points, seeds, k)
    return _blob_recall_from_neighbours(neighbours, labels)


def compute_mahalanobis_margin(engine, points: np.ndarray) -> float:
    """Tiebreaker: mean nearest-cluster Mahalanobis / gate cap. Lower = better.

    Reads ``engine._mahalanobis`` (computed once per fit) and
    ``engine._max_mahalanobis``. O(N) at most.
    """
    mah = getattr(engine, "_mahalanobis", None)
    cap = float(getattr(engine, "_max_mahalanobis", 3.0))
    if mah is None or cap <= 0.0:
        return float("inf")
    nearest = mah.min(axis=1)
    return float(nearest.mean() / cap)


def auto_tune_reg_covar(
    points: np.ndarray,
    seeds: Sequence[Sequence[int]],
    candidates: Sequence[float] = (0.1, 0.3, 1.0, 3.0, 10.0),
    rgb_threshold: float = 0.5,
    max_mahalanobis: float = 3.0,
    k: int = 10,
) -> float:
    """Sweep candidates; pick (recall desc, margin desc) winner.

    Default candidates cover the band identified in
    claudedocs/clustering-tunable-spec.md §2c. Held fixed:
    rgb_threshold, max_mahalanobis. Returns the winning reg_covar.
    """
    # Lazy import avoids a circular import at module load.
    from flake_analysis.core.clustering.engine import InteractiveClusteringEngine

    seed_dict = {cid: list(idxs) for cid, idxs in enumerate(seeds)}
    # Precompute KD-tree neighbour map once — invariant across the sweep.
    neighbours = _seed_neighbours(points, seed_dict, k)
    best_score = (-1.0, float("inf"))  # (recall, -margin)
    best_value = float(list(candidates)[0])
    for rc in candidates:
        engine = InteractiveClusteringEngine()
        result = engine.fit(
            points, list(seeds),
            rgb_threshold=rgb_threshold,
            max_mahalanobis=max_mahalanobis,
            reg_covar=float(rc),
        )
        recall = _blob_recall_from_neighbours(neighbours, result.labels)
        margin = compute_mahalanobis_margin(engine, points)
        score = (recall, -margin)
        if score > best_score:
            best_score = score
            best_value = float(rc)
    return best_value
