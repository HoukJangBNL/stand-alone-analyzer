"""Clustering schemas per backend design §1.2 + §1.3."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class SeedGroup(BaseModel):
    """A single seed group authored by the user."""
    name: str
    domain_ids: list[int]


class ClusteringRefitParams(BaseModel):
    """Mirrors pipeline/clustering.py:53-65."""
    seed_groups: list[SeedGroup]
    feature_cols: list[str] = Field(default_factory=lambda: ["mean_r", "mean_g", "mean_b"])
    covariance_type: Literal["full", "tied", "diag", "spherical"] = "full"
    rgb_threshold: float = 0.50
    fit_scope: Literal["seeds", "all_selected"] = "seeds"
    max_mahalanobis: float = 3.0


class ApplyThresholdsParams(BaseModel):
    """Mirrors pipeline/clustering.py:183-188."""
    cluster_thresholds: dict[int, float]
    max_mahalanobis: float | None = None


class ClusteringSummary(BaseModel):
    """Result wrapper used inside SSE 'done' event for /run/clustering/refit."""
    output_dir: str
    n_clusters: int
    n_assigned: int
    n_unassigned: int
    wrapper_params_hash: str | None = None


class ApplyThresholdsSummary(BaseModel):
    """Result wrapper for /run/clustering/apply_thresholds — mirrors apply_thresholds() return."""
    n_pass: int
    n_total: int
    n_clusters: int


class LabelsGroup(BaseModel):
    """One row of ``labels.json["groups"]`` per core/pipeline/clustering.py:272-286."""
    id: int
    name: str
    size: int
    mean_rgb: list[float]


class LabelsJson(BaseModel):
    """Frozen schema per core/pipeline/clustering.py:300-309 (plan v1 r7 §7.1)."""
    version: int
    n_clusters: int
    groups: list[LabelsGroup]
    assignments: dict[str, int]
    thresholds: dict[str, float]
    noise_label: int = -1
    random_state: int = 42
    fitted_at: str
    max_mahalanobis: float | None = None
