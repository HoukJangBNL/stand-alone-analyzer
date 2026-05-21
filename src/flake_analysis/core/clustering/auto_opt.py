"""Auto-optimisation metrics for the seed-driven GMM clustering engine.

Primary: blob-recall (KD-tree k-NN around seeds). Tiebreaker: Mahalanobis
self-distribution margin. Public driver: auto_tune_reg_covar.
See claudedocs/clustering-tunable-spec.md §4.
"""
from __future__ import annotations

from typing import Dict, List, Sequence

import numpy as np
from scipy.spatial import cKDTree


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
    if points.shape[0] == 0:
        return 0.0
    tree = cKDTree(points)
    per_cluster_recall: List[float] = []
    for cid, seed_idxs in seeds.items():
        seed_idxs = list(seed_idxs)
        if not seed_idxs:
            continue
        # k+1 because the nearest neighbour of a point is itself.
        _, idx = tree.query(points[seed_idxs], k=min(k + 1, points.shape[0]))
        if idx.ndim == 1:
            idx = idx.reshape(-1, 1)
        idx = idx[:, 1:]  # drop self column
        nbr_labels = labels[idx]
        share = (nbr_labels == cid).mean(axis=1)
        per_cluster_recall.append(float(share.mean()))
    if not per_cluster_recall:
        return 0.0
    return float(np.mean(per_cluster_recall))
