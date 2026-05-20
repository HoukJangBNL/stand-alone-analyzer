import pytest
from flake_analysis.api.schemas.compute import (
    ThumbnailsParams,
    BackgroundParams,
    DomainStatsParams,
    DomainProximityParams,
    ThumbnailsSummary,
)

def test_thumbnails_params_defaults():
    """ThumbnailsParams has sensible defaults."""
    p = ThumbnailsParams()
    assert p.raw_ext == ".png"
    assert p.quality == 80
    assert p.force_recompute is False

def test_thumbnails_summary_shape():
    """ThumbnailsSummary matches wrapper return shape."""
    s = ThumbnailsSummary(
        output_dir="/path/to/00_thumbnails",
        n_images=100,
        n_skipped=5,
        n_failed=2,
        params={"quality": 80},
        params_hash="sha256:abc",
        cache_dir=None,
    )
    assert s.n_images == 100
