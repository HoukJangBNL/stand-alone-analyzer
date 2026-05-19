"""Manual seed-group GMM should be deterministic with random_state=42 (Plan v1 r6 D6.1)."""
from __future__ import annotations

import numpy as np

from flake_analysis.core.clustering import InteractiveClusteringEngine


def _make_data():
    rng = np.random.default_rng(0)
    repr_rgbs = rng.uniform(0.0, 1.0, size=(100, 3))
    seed_groups = [
        [0, 1, 2],
        [50, 51, 52],
    ]
    return repr_rgbs, seed_groups


def test_gmm_seeded_reproducible():
    """Same input + same seeds → identical labels, means, weights, covariances."""
    repr_rgbs, seed_groups = _make_data()

    engine1 = InteractiveClusteringEngine()
    r1 = engine1.fit(repr_rgbs, seed_groups, rgb_threshold=0.5)

    engine2 = InteractiveClusteringEngine()
    r2 = engine2.fit(repr_rgbs, seed_groups, rgb_threshold=0.5)

    np.testing.assert_array_equal(r1.labels, r2.labels)
    np.testing.assert_allclose(r1.cluster_centers, r2.cluster_centers)
    np.testing.assert_allclose(r1.probabilities, r2.probabilities)
    if r1.covariances is not None and r2.covariances is not None:
        np.testing.assert_allclose(r1.covariances, r2.covariances)
    if r1.weights is not None and r2.weights is not None:
        np.testing.assert_allclose(r1.weights, r2.weights)


def test_refilter_does_not_refit():
    """refilter() should change labels without re-running GMM (means stable)."""
    repr_rgbs, seed_groups = _make_data()

    engine = InteractiveClusteringEngine()
    r1 = engine.fit(repr_rgbs, seed_groups, rgb_threshold=0.1)
    means_before = r1.cluster_centers.copy()

    r2 = engine.refilter(rgb_threshold=0.99)
    np.testing.assert_allclose(r2.cluster_centers, means_before)
    # Tighter threshold ⇒ at least as many unassigned domains.
    assert (r2.labels == -1).sum() >= (r1.labels == -1).sum()


def test_fit_rejects_empty_seed_groups():
    repr_rgbs, _ = _make_data()
    engine = InteractiveClusteringEngine()
    try:
        engine.fit(repr_rgbs, [], rgb_threshold=0.5)
    except ValueError:
        return
    raise AssertionError("expected ValueError when seed_groups is empty")
