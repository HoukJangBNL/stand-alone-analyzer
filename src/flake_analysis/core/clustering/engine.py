"""Interactive clustering engine — in-memory cluster fit + refilter.

User selects seed groups via lasso on scatter plots. Engine initializes
a GMM from seed group statistics, fits via EM, then refilters membership
assignments by repr_rgb posterior probability without re-fitting.

Extracted from Qpress ``modules/analyzer/interactive_clustering/engine.py``.
The only edits vs the Qpress source:

  * ``msg`` import points at the standalone shim
    (``flake_analysis.core._compat.msg``).
  * ``InteractiveClusterResult`` dataclass is inlined here (Qpress's
    ``models.py`` also defined ``ClusterResult`` / ``ConfidenceResult``
    for the BIC pathway, which is **not** migrated to the standalone —
    only this seed-group ``means_init`` pathway is in scope).
  * ``random_state=42`` matches Qpress (Plan v1 r6 D6.1).
"""

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
from sklearn.mixture import GaussianMixture

from flake_analysis.core._compat import msg


@dataclass
class InteractiveClusterResult:
    """Result of interactive GMM clustering with dual-filter exclusion."""

    labels: np.ndarray          # (N,) int, -1 for unassigned
    probabilities: np.ndarray   # (N,) float, max posterior per flake
    cluster_centers: np.ndarray  # (K, 3) float, GMM means
    n_clusters: int
    covariances: Optional[np.ndarray] = None   # (K, 3, 3) GMM covariances
    weights: Optional[np.ndarray] = None       # (K,) GMM component weights
    thresholds: Optional[list] = None          # per-cluster probability thresholds


class InteractiveClusteringEngine:
    """Seed-based GMM clustering engine with refilter support.

    Usage:
        engine = InteractiveClusteringEngine()
        result = engine.fit(repr_rgbs, seed_groups, rgb_threshold=0.1)
        # Adjust threshold without re-running GMM:
        result = engine.refilter(rgb_threshold=0.2)
    """

    def __init__(self):
        self._gmm: Optional[GaussianMixture] = None
        self._repr_rgbs: Optional[np.ndarray] = None
        self._raw_posteriors: Optional[np.ndarray] = None  # (N, K) from GMM
        self._thresholds: List[float] = []

    def fit(
        self,
        repr_rgbs: np.ndarray,
        seed_groups: List[List[int]],
        rgb_threshold: float = 0.1,
        thresholds: Optional[List[float]] = None,
        max_iter: int = 100,
        tol: float = 1e-4,
        fit_scope: str = "seeds",
    ) -> InteractiveClusterResult:
        """Fit GMM on the seed-defined subset, then score everyone.

        Args:
            repr_rgbs: (N, 3) array of representative RGB values per flake.
            seed_groups: List of K groups, each a list of *positional* flake
                indices into ``repr_rgbs`` (i.e. row indices, NOT domain ids).
                Plan v1 r6 D6.2: callers that pass ``domain_id`` based seeds
                must convert them to positional indices before invoking ``fit``.
            rgb_threshold: Posterior probability cutoff for Filter 1 (broadcast
                to all clusters when ``thresholds`` is not provided).
            thresholds: Per-cluster probability thresholds. Overrides
                ``rgb_threshold`` when provided.
            max_iter: Maximum EM iterations.
            tol: EM convergence tolerance.
            fit_scope: ``"seeds"`` (default, recommended) trains the GMM on
                the union of seed-group members only — covariances stay tight
                around the user's seeds, and non-seed selector-passing domains
                far from any seed get low posteriors and are auto-rejected by
                Filter 1. ``"all"`` is the legacy behaviour: train on every
                ``repr_rgbs`` row, with seeds providing only ``means_init``.
                Use "all" if you have very few seeds and need EM to discover
                the broader distribution; default to "seeds" otherwise.

        Returns:
            InteractiveClusterResult with labels, probabilities, centers.

        Raises:
            ValueError: If fewer than 1 seed group provided, ``fit_scope`` is
                unrecognised, or the seeded subset is too small for the
                requested ``k``.
        """
        if len(seed_groups) < 1:
            raise ValueError(f"Need at least 1 seed group, got {len(seed_groups)}")
        if fit_scope not in ("seeds", "all"):
            raise ValueError(
                f"fit_scope must be 'seeds' or 'all', got {fit_scope!r}"
            )

        k = len(seed_groups)
        self._repr_rgbs = repr_rgbs

        # Set per-cluster thresholds
        if thresholds is not None:
            self._thresholds = list(thresholds)
        else:
            self._thresholds = [rgb_threshold] * k

        # Compute initial GMM parameters from seed groups
        means_init = np.zeros((k, 3), dtype=np.float64)

        for i, indices in enumerate(seed_groups):
            seed_rgbs = repr_rgbs[indices]
            means_init[i] = seed_rgbs.mean(axis=0)

        # Build the actual fit-input array. ``"seeds"`` uses only the
        # union of seed members so EM cannot drag covariances toward
        # non-seed selector-passing domains (user feedback: "시드 근처
        # 가 아닌데도 selection 됐으면 다 피팅이 되고 있잖아"). ``"all"``
        # falls back to the legacy behaviour for compatibility.
        if fit_scope == "seeds":
            seed_idx_concat = np.unique(np.concatenate(
                [np.asarray(g, dtype=np.int64) for g in seed_groups]
                or [np.array([], dtype=np.int64)]
            ))
            if seed_idx_concat.size < k:
                raise ValueError(
                    f"fit_scope='seeds' needs at least {k} unique seed "
                    f"members (got {seed_idx_concat.size}); add more "
                    f"domains to your seed groups or pass fit_scope='all'."
                )
            fit_input = repr_rgbs[seed_idx_concat]
            msg.info(
                f"Fitting {k}-component GMM on seeds only "
                f"({fit_input.shape[0]} domains); will score all "
                f"{len(repr_rgbs)} via predict_proba"
            )
        else:
            fit_input = repr_rgbs
            msg.info(
                f"Fitting {k}-component GMM on full set "
                f"({fit_input.shape[0]} domains, legacy fit_scope='all')"
            )

        self._gmm = GaussianMixture(
            n_components=k,
            covariance_type="full",
            means_init=means_init,
            max_iter=max_iter,
            tol=tol,
            n_init=1,
            random_state=42,
        )
        self._gmm.fit(fit_input)

        # Compute posteriors for ALL flakes (the seeds drove the fit;
        # non-seed selector-passing domains get scored against the
        # seed-tightened model and are filtered by threshold).
        self._raw_posteriors = self._gmm.predict_proba(repr_rgbs)

        msg.info(f"GMM converged in {self._gmm.n_iter_} iterations")

        return self._apply_filters()

    def refilter(
        self,
        rgb_threshold: Optional[float] = None,
        thresholds: Optional[List[float]] = None,
    ) -> InteractiveClusterResult:
        """Re-apply filters with new thresholds without re-running GMM.

        Args:
            rgb_threshold: New posterior probability cutoff (broadcast to all
                clusters). Ignored when ``thresholds`` is provided.
            thresholds: Per-cluster probability thresholds. Overrides
                ``rgb_threshold`` when provided.

        Returns:
            Updated InteractiveClusterResult.

        Raises:
            RuntimeError: If fit() has not been called.
        """
        if self._gmm is None or self._raw_posteriors is None:
            raise RuntimeError("Must call fit() before refilter()")

        if thresholds is not None:
            self._thresholds = list(thresholds)
        elif rgb_threshold is not None:
            k = self._gmm.n_components
            self._thresholds = [rgb_threshold] * k

        return self._apply_filters()

    def _apply_filter1(self) -> np.ndarray:
        """Apply Filter 1 with per-cluster probability thresholds."""
        labels = self._raw_posteriors.argmax(axis=1)  # (N,)
        for k in range(self._gmm.n_components):
            threshold = self._thresholds[k] if k < len(self._thresholds) else 0.5
            cluster_mask = labels == k
            cluster_posteriors = self._raw_posteriors[cluster_mask, k]
            below_threshold = cluster_posteriors < threshold
            indices = np.where(cluster_mask)[0]
            labels[indices[below_threshold]] = -1
        return labels

    def _apply_filters(self) -> InteractiveClusterResult:
        """Apply Filter 1 and return result."""
        labels = self._apply_filter1()
        max_posteriors = self._raw_posteriors.max(axis=1)

        return InteractiveClusterResult(
            labels=labels,
            probabilities=max_posteriors,
            cluster_centers=self._gmm.means_.copy(),
            n_clusters=self._gmm.n_components,
            covariances=self._gmm.covariances_.copy(),
            weights=self._gmm.weights_.copy(),
            thresholds=list(self._thresholds),
        )
