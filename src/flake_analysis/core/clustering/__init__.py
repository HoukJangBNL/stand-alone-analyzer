"""Manual seed-group GMM clustering (means_init pathway only).

Note: Qpress's BIC pathway, confidence scoring, and validation metrics
remain in Qpress and are deliberately NOT migrated to the standalone
package per plan v1 r6 §4.
"""
from flake_analysis.core.clustering.engine import (
    InteractiveClusteringEngine,
    InteractiveClusterResult,
)

__all__ = [
    "InteractiveClusteringEngine",
    "InteractiveClusterResult",
]
