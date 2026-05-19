"""M1 PR 1.2 import smoke tests."""
from __future__ import annotations


def test_color_classification_import():
    from flake_analysis.core.color_classification import (  # noqa: F401
        compute_and_cache_stats_from_flakes,
    )


def test_clustering_import():
    from flake_analysis.core.clustering import (  # noqa: F401
        InteractiveClusterResult,
        InteractiveClusteringEngine,
    )


def test_pipeline_full_import():
    from flake_analysis.core.pipeline import (  # noqa: F401
        run_background,
        run_clustering,
        run_domain_proximity,
        run_domain_stats,
        run_selector,
    )
