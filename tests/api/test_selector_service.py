from pathlib import Path
import pandas as pd
import pytest

from flake_analysis.api.services.selector_service import apply_brush_intersection


def _write_selection(p: Path, rows: list[tuple[int, bool]]) -> None:
    df = pd.DataFrame(rows, columns=["domain_id", "selected"])
    df.to_parquet(p, index=False)


def test_no_lasso_keeps_pipeline_output(tmp_path):
    """lasso_ids=None → return original count, file unchanged."""
    p = tmp_path / "selection.parquet"
    _write_selection(p, [(1, True), (2, True), (3, False)])

    n = apply_brush_intersection(p, lasso_ids=None)
    assert n == 2

    df = pd.read_parquet(p)
    assert df.loc[df["domain_id"] == 1, "selected"].iat[0] == True
    assert df.loc[df["domain_id"] == 2, "selected"].iat[0] == True


def test_empty_lasso_keeps_pipeline_output(tmp_path):
    """lasso_ids=[] is treated as "no brush" (matches tab_selector.py:773)."""
    p = tmp_path / "selection.parquet"
    _write_selection(p, [(1, True), (2, True)])

    n = apply_brush_intersection(p, lasso_ids=[])
    assert n == 2


def test_lasso_intersection_tightens(tmp_path):
    """Brush ∩ filter — domains lassoed but rejected by filter stay rejected."""
    p = tmp_path / "selection.parquet"
    _write_selection(p, [(1, True), (2, True), (3, False), (4, True)])

    # Lasso [2, 3, 5] — only 2 is in filter; 3 was rejected; 5 doesn't exist.
    n = apply_brush_intersection(p, lasso_ids=[2, 3, 5])
    assert n == 1

    df = pd.read_parquet(p)
    assert df.loc[df["domain_id"] == 1, "selected"].iat[0] == False
    assert df.loc[df["domain_id"] == 2, "selected"].iat[0] == True
    assert df.loc[df["domain_id"] == 3, "selected"].iat[0] == False
    assert df.loc[df["domain_id"] == 4, "selected"].iat[0] == False


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        apply_brush_intersection(tmp_path / "nope.parquet", lasso_ids=[1])
