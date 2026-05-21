import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

from flake_analysis.api.services.clustering_service import (
    load_labels_json,
    load_assignments_table,
    load_seed_groups,
)


def _write_labels(folder: Path) -> None:
    (folder / "04_clustering").mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "n_clusters": 2,
        "groups": [
            {"id": 0, "name": "a", "size": 10, "mean_rgb": [0.1, 0.2, 0.3]},
            {"id": 1, "name": "b", "size": 5, "mean_rgb": [0.4, 0.5, 0.6]},
        ],
        "assignments": {"1": 0, "2": 1, "3": 0},
        "thresholds": {"0": 0.5, "1": 0.6},
        "noise_label": -1,
        "random_state": 42,
        "fitted_at": "2026-05-21T00:00:00Z",
    }
    (folder / "04_clustering" / "labels.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_assignments(folder: Path) -> None:
    (folder / "04_clustering").mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({
        "domain_id": [1, 2, 3],
        "cluster_label": [0, 1, 0],
        "max_posterior": [0.95, 0.80, 0.70],
        "nearest_mahalanobis": [0.5, 1.2, 2.8],
    })
    df.to_parquet(folder / "04_clustering" / "assignments.parquet", index=False)


def _write_seed_groups(folder: Path) -> None:
    (folder / "04_clustering").mkdir(parents=True, exist_ok=True)
    payload = [
        {"name": "thin", "domain_ids": [1, 2, 3]},
        {"name": "thick", "domain_ids": [4, 5]},
    ]
    (folder / "04_clustering" / "seed_groups.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_load_labels_json_round_trip(tmp_path: Path):
    _write_labels(tmp_path)
    obj = load_labels_json(tmp_path)
    assert obj["n_clusters"] == 2
    assert obj["thresholds"]["0"] == 0.5


def test_load_labels_json_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_labels_json(tmp_path)


def test_load_assignments_table_returns_arrow(tmp_path: Path):
    _write_assignments(tmp_path)
    table = load_assignments_table(tmp_path)
    assert isinstance(table, pa.Table)
    assert set(table.column_names) >= {"domain_id", "cluster_label", "max_posterior"}
    ids = table.column("domain_id").to_pylist()
    assert ids == [1, 2, 3]


def test_load_assignments_table_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_assignments_table(tmp_path)


def test_load_seed_groups_round_trip(tmp_path: Path):
    _write_seed_groups(tmp_path)
    groups = load_seed_groups(tmp_path)
    assert len(groups) == 2
    assert groups[0]["name"] == "thin"
    assert groups[0]["domain_ids"] == [1, 2, 3]


def test_load_seed_groups_missing_returns_empty(tmp_path: Path):
    # Missing file is not an error — empty list is the autoload contract.
    assert load_seed_groups(tmp_path) == []
