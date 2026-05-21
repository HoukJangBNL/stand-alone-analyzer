"""Unit tests for clustering auto-opt metrics (W4.4)."""
from __future__ import annotations

import time

import numpy as np

from flake_analysis.core.clustering.auto_opt import compute_blob_recall


def _two_clean_blobs(rng_seed: int = 7):
    rng = np.random.default_rng(rng_seed)
    a = rng.normal(loc=[50.0, 50.0, 50.0], scale=2.0, size=(50, 3))
    b = rng.normal(loc=[200.0, 200.0, 200.0], scale=2.0, size=(50, 3))
    return np.vstack([a, b]).astype(np.float64)


def test_compute_blob_recall_perfect_when_label_matches_neighbourhood():
    points = _two_clean_blobs()
    # Seeds: 3 members per blob, both labelled correctly with full coverage.
    seeds = {0: [0, 1, 2], 1: [50, 51, 52]}
    labels = np.array([0] * 50 + [1] * 50, dtype=np.int64)
    recall = compute_blob_recall(points, seeds, labels, k=10)
    assert recall == 1.0


def test_compute_blob_recall_zero_when_neighbours_all_noise():
    points = _two_clean_blobs()
    seeds = {0: [0, 1, 2], 1: [50, 51, 52]}
    # Only seed members labelled; all other neighbours are -1.
    labels = np.full(100, -1, dtype=np.int64)
    for cid, idxs in seeds.items():
        for i in idxs:
            labels[i] = cid
    recall = compute_blob_recall(points, seeds, labels, k=10)
    # k=10 neighbourhood around each seed includes 7 non-seed points → noise.
    # Per-seed recall ~ 3/10. Mean across seeds well below 0.5.
    assert recall < 0.5


def test_compute_blob_recall_handles_empty_seed_group():
    points = _two_clean_blobs()
    seeds = {0: [0, 1, 2], 1: []}  # empty group
    labels = np.array([0] * 50 + [1] * 50, dtype=np.int64)
    # Empty groups skipped; recall computed over remaining groups.
    recall = compute_blob_recall(points, seeds, labels, k=10)
    assert 0.0 <= recall <= 1.0


def test_compute_blob_recall_latency_under_100ms_at_n1000():
    rng = np.random.default_rng(0)
    points = rng.normal(size=(1000, 3))
    seeds = {0: list(range(0, 5)), 1: list(range(500, 505))}
    labels = rng.integers(-1, 2, size=1000).astype(np.int64)
    t0 = time.perf_counter()
    compute_blob_recall(points, seeds, labels, k=10)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.1, f"blob-recall took {elapsed:.3f}s at N=1000"
