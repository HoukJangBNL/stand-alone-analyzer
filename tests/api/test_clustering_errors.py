from flake_analysis.api.errors import ClusteringNotFitted, SeedGroupsMissing


def test_clustering_not_fitted_envelope():
    e = ClusteringNotFitted(expected_path="/x/y/labels.json")
    body = e.to_response()
    assert body["error"]["code"] == "clustering_not_fitted"
    assert e.status_code == 404


def test_seed_groups_missing_envelope():
    e = SeedGroupsMissing(expected_path="/x/y/seed_groups.json")
    body = e.to_response()
    assert body["error"]["code"] == "seed_groups_missing"
    assert e.status_code == 404
