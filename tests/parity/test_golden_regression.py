"""Numerical regression test against frozen golden outputs.

Runs the canonical synthetic fixture pipeline and compares every
non-volatile output to the golden record under ``tests/parity/golden/``.

Any silent change in ``flake_analysis.core`` numerics — RGB stats,
GMM fit, posterior thresholds, pair distances, flake construction —
will surface here.

Golden records were frozen at commit ``e2a2ede`` on 2026-05-21.
See ``regenerate_golden.py`` and ``README.md`` for the regen procedure.

How comparisons work
--------------------
* **SHA256 records** (background.npy, gmm_model.pkl): exact bytes
  match. Most sensitive — picks up *any* change.
* **Array records** (stats.npz keys): per-element ``np.allclose`` for
  floats (tight rtol/atol — same code on same fixture should be
  bit-equal in practice but a small tolerance covers reduction-order
  edge cases on different CPUs), exact for integer dtypes.
* **DataFrame records** (parquet files): per-column comparison —
  numeric columns via ``np.allclose``, integer/object columns via
  exact equality, dtype string compared exactly.
* **JSON records** (labels.json): dict equality after stripping the
  declared volatile fields (``fitted_at``).

Tolerances
----------
``RTOL = 1e-10`` and ``ATOL = 1e-12`` — much tighter than the algo
domain rule's default (``rtol=1e-5, atol=1e-8``). Rationale: this
test runs the *same* code on the *same* deterministic fixture, so
output should be reproducible to machine precision. The narrow
tolerance is what lets it catch subtle drift; if a code change
genuinely needs more slack, the regenerator must be re-run with
explicit user sign-off (see README §Regeneration).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pytest

from tests.parity.regenerate_golden import (
    GOLDEN_DIR,
    GOLDEN_SCHEMA_VERSION,
    LABELS_VOLATILE_FIELDS,
    _sha256_file,
    _strip_volatile,
    run_canonical_pipeline,
)


RTOL = 1e-10
ATOL = 1e-12


# ---------------------------------------------------------------------------
# Fixture: run the canonical pipeline once and reuse across tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def canonical_run(tmp_path_factory):
    """Run the canonical fixture pipeline once for the whole module."""
    tmp = tmp_path_factory.mktemp("golden_check")
    analysis = run_canonical_pipeline(Path(tmp))
    return analysis


@pytest.fixture(scope="module")
def golden() -> Dict[str, Any]:
    p = GOLDEN_DIR / "pipeline_golden.json"
    if not p.exists():
        pytest.fail(
            f"Golden record missing at {p}. Run "
            "`python -m tests.parity.regenerate_golden` to create it."
        )
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("schema_version") != GOLDEN_SCHEMA_VERSION:
        pytest.fail(
            f"Golden schema version mismatch: file={data.get('schema_version')} "
            f"code={GOLDEN_SCHEMA_VERSION}. Regenerate or update the test."
        )
    return data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_float_dtype(dtype: str) -> bool:
    return dtype.startswith("float")


def _is_integer_dtype(dtype: str) -> bool:
    return dtype.startswith("int") or dtype.startswith("uint")


def _assert_array_equal_to_record(
    actual: np.ndarray, record: Dict[str, Any], where: str,
) -> None:
    expected_dtype = record["dtype"]
    expected_shape = tuple(record["shape"])
    expected_values = record["values"]

    assert str(actual.dtype) == expected_dtype, (
        f"{where}: dtype mismatch — actual={actual.dtype} expected={expected_dtype}"
    )
    assert tuple(actual.shape) == expected_shape, (
        f"{where}: shape mismatch — actual={actual.shape} expected={expected_shape}"
    )

    flat_actual = actual.flatten()
    flat_expected = np.asarray(expected_values, dtype=actual.dtype)

    if _is_float_dtype(expected_dtype):
        np.testing.assert_allclose(
            flat_actual, flat_expected, rtol=RTOL, atol=ATOL,
            err_msg=f"{where}: float values diverged",
        )
    else:
        np.testing.assert_array_equal(
            flat_actual, flat_expected, err_msg=f"{where}: exact values diverged",
        )


def _assert_dataframe_equal_to_record(
    actual: pd.DataFrame, record: Dict[str, Any], where: str,
) -> None:
    expected_n = record["n_rows"]
    expected_cols: List[str] = record["columns"]
    expected_data = record["data"]

    assert len(actual) == expected_n, (
        f"{where}: row count mismatch — actual={len(actual)} expected={expected_n}"
    )
    assert list(actual.columns) == expected_cols, (
        f"{where}: columns mismatch — actual={list(actual.columns)} "
        f"expected={expected_cols}"
    )

    for col in expected_cols:
        col_record = expected_data[col]
        expected_dtype = col_record["dtype"]
        expected_values = col_record["values"]

        actual_series = actual[col]
        assert str(actual_series.dtype) == expected_dtype, (
            f"{where}.{col}: dtype mismatch — actual={actual_series.dtype} "
            f"expected={expected_dtype}"
        )

        if _is_float_dtype(expected_dtype):
            np.testing.assert_allclose(
                actual_series.to_numpy(),
                np.asarray(expected_values, dtype=np.float64),
                rtol=RTOL, atol=ATOL,
                err_msg=f"{where}.{col}: float values diverged",
            )
        elif _is_integer_dtype(expected_dtype):
            np.testing.assert_array_equal(
                actual_series.to_numpy(),
                np.asarray(expected_values, dtype=actual_series.dtype),
                err_msg=f"{where}.{col}: integer values diverged",
            )
        elif expected_dtype == "bool":
            np.testing.assert_array_equal(
                actual_series.to_numpy(),
                np.asarray(expected_values, dtype=bool),
                err_msg=f"{where}.{col}: bool values diverged",
            )
        else:
            # object / string / category — exact equality
            assert actual_series.tolist() == expected_values, (
                f"{where}.{col}: object values diverged"
            )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_background_matches_golden(canonical_run, golden):
    """01_background/background.npy must hash-match the golden record."""
    bg_path = canonical_run / "01_background" / "background.npy"
    record = golden["background"]
    actual_hash = _sha256_file(bg_path)
    assert actual_hash == record["sha256"], (
        f"background.npy SHA256 changed.\n"
        f"  actual:   {actual_hash}\n"
        f"  expected: {record['sha256']}\n"
        f"  golden diagnostics: {record['diagnostics']}"
    )


def test_domain_stats_matches_golden(canonical_run, golden):
    """02_domain_stats/stats.npz arrays must match the golden record."""
    npz = np.load(canonical_run / "02_domain_stats" / "stats.npz")
    record = golden["domain_stats"]["arrays"]
    assert set(npz.files) == set(record.keys()), (
        f"stats.npz keys changed: actual={set(npz.files)} "
        f"expected={set(record.keys())}"
    )
    for key, arr_record in record.items():
        _assert_array_equal_to_record(
            npz[key], arr_record, where=f"stats.npz[{key!r}]",
        )


def test_selector_matches_golden(canonical_run, golden):
    """03_selector/selection.parquet must match the golden record."""
    df = pd.read_parquet(canonical_run / "03_selector" / "selection.parquet")
    _assert_dataframe_equal_to_record(
        df, golden["selector"]["data"], where="selection.parquet",
    )


def test_clustering_labels_matches_golden(canonical_run, golden):
    """04_clustering/labels.json (volatile fields stripped) must match."""
    labels = json.loads(
        (canonical_run / "04_clustering" / "labels.json").read_text(encoding="utf-8")
    )
    actual = _strip_volatile(labels)
    expected = golden["clustering"]["labels"]["data"]
    assert actual == expected, (
        f"labels.json content diverged from golden "
        f"(after stripping {LABELS_VOLATILE_FIELDS}).\n"
        f"  actual:   {json.dumps(actual, sort_keys=True)}\n"
        f"  expected: {json.dumps(expected, sort_keys=True)}"
    )


def test_clustering_assignments_matches_golden(canonical_run, golden):
    """04_clustering/assignments.parquet must match the golden record."""
    df = pd.read_parquet(canonical_run / "04_clustering" / "assignments.parquet")
    _assert_dataframe_equal_to_record(
        df, golden["clustering"]["assignments"]["data"],
        where="assignments.parquet",
    )


def test_clustering_gmm_model_matches_golden(canonical_run, golden):
    """04_clustering/gmm_model.pkl must hash-match the golden record."""
    pkl_path = canonical_run / "04_clustering" / "gmm_model.pkl"
    expected = golden["clustering"]["gmm_model"]["sha256"]
    actual = _sha256_file(pkl_path)
    assert actual == expected, (
        f"gmm_model.pkl SHA256 changed.\n"
        f"  actual:   {actual}\n"
        f"  expected: {expected}"
    )


def test_domain_proximity_distances_matches_golden(canonical_run, golden):
    """05_domain_proximity/distances.parquet must match the golden record."""
    df = pd.read_parquet(
        canonical_run / "05_domain_proximity" / "distances.parquet"
    )
    _assert_dataframe_equal_to_record(
        df, golden["domain_proximity"]["distances"]["data"],
        where="distances.parquet",
    )


def test_domain_proximity_flake_assignments_matches_golden(canonical_run, golden):
    """05_domain_proximity/flake_assignments.parquet must match the golden record."""
    df = pd.read_parquet(
        canonical_run / "05_domain_proximity" / "flake_assignments.parquet"
    )
    _assert_dataframe_equal_to_record(
        df, golden["domain_proximity"]["flake_assignments"]["data"],
        where="flake_assignments.parquet",
    )
