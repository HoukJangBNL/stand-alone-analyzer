# tests/api/test_clustering_schemas.py
import pytest
from pydantic import ValidationError

from flake_analysis.api.schemas.clustering import (
    SeedGroup,
    ClusteringRefitParams,
    ApplyThresholdsParams,
    ClusteringSummary,
    ApplyThresholdsSummary,
    LabelsJson,
)


def test_seed_group_round_trip():
    sg = SeedGroup(name="thin", domain_ids=[1, 2, 3])
    assert sg.name == "thin"
    assert sg.domain_ids == [1, 2, 3]


def test_refit_params_defaults_match_pipeline():
    p = ClusteringRefitParams(seed_groups=[SeedGroup(name="a", domain_ids=[1, 2])])
    assert p.feature_cols == ["mean_r", "mean_g", "mean_b"]
    assert p.covariance_type == "full"
    assert p.rgb_threshold == 0.50
    assert p.fit_scope == "seeds"
    assert p.max_mahalanobis == 3.0


def test_refit_params_validates_fit_scope():
    with pytest.raises(ValidationError):
        ClusteringRefitParams(
            seed_groups=[SeedGroup(name="a", domain_ids=[1])],
            fit_scope="garbage",
        )


def test_apply_thresholds_params_optional_max_mah():
    p = ApplyThresholdsParams(cluster_thresholds={0: 0.5, 1: 0.6})
    assert p.max_mahalanobis is None
    p2 = ApplyThresholdsParams(cluster_thresholds={0: 0.5}, max_mahalanobis=2.5)
    assert p2.max_mahalanobis == 2.5


def test_clustering_summary_shape():
    s = ClusteringSummary(
        output_dir="/tmp/04_clustering",
        n_clusters=3,
        n_assigned=120,
        n_unassigned=30,
        wrapper_params_hash="abc",
    )
    assert s.n_clusters == 3


def test_apply_thresholds_summary_shape():
    s = ApplyThresholdsSummary(n_pass=80, n_total=150, n_clusters=3)
    assert s.n_pass == 80


def test_labels_json_groups_required():
    payload = {
        "version": 1,
        "n_clusters": 2,
        "groups": [
            {"id": 0, "name": "a", "size": 10, "mean_rgb": [0.1, 0.2, 0.3]},
            {"id": 1, "name": "b", "size": 5, "mean_rgb": [0.4, 0.5, 0.6]},
        ],
        "assignments": {"1": 0, "2": 1},
        "thresholds": {"0": 0.5, "1": 0.6},
        "noise_label": -1,
        "random_state": 42,
        "fitted_at": "2026-05-21T00:00:00Z",
    }
    obj = LabelsJson.model_validate(payload)
    assert obj.n_clusters == 2
    assert len(obj.groups) == 2
    assert obj.thresholds == {"0": 0.5, "1": 0.6}
