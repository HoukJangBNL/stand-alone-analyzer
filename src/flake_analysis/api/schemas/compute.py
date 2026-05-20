"""Compute step schemas per backend design §1.2."""
from __future__ import annotations
from pydantic import BaseModel
from typing import Generic, TypeVar

T = TypeVar("T")

class RunResult(BaseModel, Generic[T]):
    """Generic SSE 'done' event result wrapper."""
    result: T

class ThumbnailsParams(BaseModel):
    """POST /run/thumbnails body."""
    raw_ext: str = ".png"
    quality: int = 80
    force_recompute: bool = False

class ThumbnailsSummary(BaseModel):
    """Thumbnails step return dict shape."""
    output_dir: str
    n_images: int
    n_skipped: int
    n_failed: int
    params: dict
    params_hash: str | None
    cache_dir: str | None

class BackgroundParams(BaseModel):
    """POST /run/background body."""
    seed: int = 0
    max_images: int = 100
    gaussian_sigma: float = 10.0
    method: str = "median"

class BackgroundSummary(BaseModel):
    """Background step return dict shape."""
    output_path: str
    shape: tuple[int, int, int] | None
    params: dict

class DomainStatsParams(BaseModel):
    """POST /run/domain_stats body."""
    repr_mode: str = "median"
    raw_ext: str = ".png"

class DomainStatsSummary(BaseModel):
    """Domain stats step return dict shape."""
    output_path: str
    num_flakes: int
    params: dict

class DomainProximityParams(BaseModel):
    """POST /run/domain_proximity body."""
    r_max_px: float = 200.0
    min_area_px: int = 10
    max_area_px: int | None = None
    d_touch_px: float = 2.0
    pixel_size_um: float = 0.5
    link_distance_um: float = 5.0
    workers: int = 4

class DomainProximitySummary(BaseModel):
    """Domain proximity step return dict shape."""
    distances_path: str
    flake_assignments_path: str
    n_pairs: int
    n_domains: int
    n_flakes: int
    params: dict
