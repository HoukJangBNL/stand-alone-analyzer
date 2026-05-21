# tests/api/test_selector_schemas.py
from flake_analysis.api.schemas.selector import (
    SelectorParams,
    SelectorSummary,
    SelectorCommitRequest,
    SelectorCommitSummary,
    METRIC_DEFS,
)


def test_selector_params_all_optional():
    """All bounds are optional — None means unbounded (matches pipeline/selector.py:29-43)."""
    p = SelectorParams()
    assert p.area_min is None
    assert p.area_max is None
    assert p.std_r_min is None
    assert p.sam2_max is None


def test_selector_params_partial():
    """Partial bounds still parse."""
    p = SelectorParams(area_min=10.0, std_r_max=20.0)
    assert p.area_min == 10.0
    assert p.std_r_max == 20.0
    assert p.area_max is None


def test_selector_summary_shape():
    s = SelectorSummary(
        output_path="/p/03_selector/selection.parquet",
        selected_count=42,
        total_count=100,
        params={"area_min": 10.0},
        params_hash="sha256:abc",
    )
    assert s.selected_count == 42


def test_commit_request_lasso_optional():
    req = SelectorCommitRequest(params=SelectorParams(area_min=5.0), lasso_ids=None)
    assert req.lasso_ids is None
    req2 = SelectorCommitRequest(params=SelectorParams(), lasso_ids=[1, 2, 3])
    assert req2.lasso_ids == [1, 2, 3]


def test_commit_summary_shape():
    s = SelectorCommitSummary(
        output_path="/p/03_selector/selection.parquet",
        n_committed=5,
        n_filter_accepted=10,
        n_lasso=7,
        total_count=100,
        params_hash="sha256:abc",
    )
    assert s.n_committed == 5


def test_metric_defs_has_five_entries():
    """Ports tab_selector.py:92-98."""
    keys = [d["key"] for d in METRIC_DEFS]
    assert keys == ["area", "std_r", "std_g", "std_b", "sam2"]
