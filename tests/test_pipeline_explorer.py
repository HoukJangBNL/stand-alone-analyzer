"""Smoke tests for the Explorer pipeline wrapper.

Per plan v1 r9 §M2 PR 2.5.

The wrapper persists Include/Exclude + NeighborFilter state to
``06_explorer/explorer_state.json`` and (optionally) the post-filter
flake_id list to ``06_explorer/selected_flakes.parquet``. Verifies:

* Both files are written when both prereqs (clustering + domain_proximity)
  are committed.
* Manifest is updated with the explorer StepEntry, including upstream
  input_hashes.
* Round-trip via ``load_explorer_state``.
* Prereq guards: missing clustering or domain_proximity raises.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from flake_analysis.pipeline.explorer import (
    load_explorer_state,
    save_explorer_state,
)
from flake_analysis.state.manifest import (
    Manifest,
    StepEntry,
    load_manifest,
    save_manifest,
)


def _setup_fixture(tmp: Path) -> Path:
    """Create labels.json + assignments.parquet + flake_assignments.parquet
    + a manifest with both clustering and domain_proximity committed.
    """
    analysis = tmp / "analysis"
    (analysis / "04_clustering").mkdir(parents=True)
    (analysis / "05_domain_proximity").mkdir(parents=True)

    labels = {
        "version": 1,
        "n_clusters": 2,
        "groups": [
            {"id": 0, "name": "graphite", "size": 5, "mean_rgb": [50, 50, 50]},
            {"id": 1, "name": "thin_layer", "size": 3, "mean_rgb": [200, 200, 200]},
        ],
        "assignments": {"0": 0, "1": 0, "2": 1},
        "thresholds": {"0": 0.5, "1": 0.5},
        "noise_label": -1,
        "random_state": 42,
        "fitted_at": "2026-05-18T10:00:00Z",
    }
    (analysis / "04_clustering" / "labels.json").write_text(
        json.dumps(labels), encoding="utf-8"
    )

    pd.DataFrame({
        "domain_id": [0, 1, 2],
        "cluster_id": [0, 0, 1],
        "posterior_p": [0.9, 0.85, 0.95],
    }).to_parquet(
        analysis / "04_clustering" / "assignments.parquet",
        engine="pyarrow",
        index=False,
    )

    pd.DataFrame({
        "domain_id": [0, 1, 2],
        "flake_id": [100, 100, 200],
        "image_id": [1, 1, 1],
    }).to_parquet(
        analysis / "05_domain_proximity" / "flake_assignments.parquet",
        engine="pyarrow",
        index=False,
    )

    m = Manifest(
        analysis_folder=str(analysis),
        steps={
            "clustering": StepEntry(
                completed_at="2026-05-18T10:00:00Z",
                params={},
                params_hash="sha256:c",
                outputs={"labels_json": "04_clustering/labels.json"},
            ),
            "domain_proximity": StepEntry(
                completed_at="2026-05-18T10:00:00Z",
                params={},
                params_hash="sha256:p",
                outputs={
                    "flake_assignments_parquet":
                        "05_domain_proximity/flake_assignments.parquet"
                },
            ),
        },
    )
    save_manifest(m, analysis)
    return analysis


def test_save_and_load_explorer_state():
    with tempfile.TemporaryDirectory() as tmp_str:
        analysis = _setup_fixture(Path(tmp_str))
        result = save_explorer_state(
            analysis_folder=str(analysis),
            include_labels=["graphite"],
            exclude_labels=[],
            neighbor_filter={"size_enabled": False},
            selected_flake_ids=[100, 200],
        )
        # Files written.
        assert (analysis / "06_explorer" / "explorer_state.json").exists()
        assert (analysis / "06_explorer" / "selected_flakes.parquet").exists()
        # Returned summary.
        assert result["selected_count"] == 2

        # Round-trip.
        loaded = load_explorer_state(str(analysis))
        assert loaded is not None
        assert loaded["include_labels"] == ["graphite"]
        assert loaded["exclude_labels"] == []
        assert "saved_at" in loaded

        # Manifest updated with upstream input_hashes.
        m = load_manifest(analysis)
        assert "explorer" in m.steps
        explorer_entry = m.steps["explorer"]
        assert explorer_entry.completed_at is not None
        assert explorer_entry.input_hashes["clustering_params_hash"] == "sha256:c"
        assert explorer_entry.input_hashes["domain_proximity_params_hash"] == "sha256:p"
        assert (
            explorer_entry.outputs["explorer_state_json"]
            == "06_explorer/explorer_state.json"
        )
        assert (
            explorer_entry.outputs["selected_flakes_parquet"]
            == "06_explorer/selected_flakes.parquet"
        )


def test_save_explorer_state_without_clustering_raises():
    with tempfile.TemporaryDirectory() as tmp_str:
        analysis = Path(tmp_str) / "analysis"
        analysis.mkdir()
        with pytest.raises(RuntimeError, match="Clustering"):
            save_explorer_state(
                analysis_folder=str(analysis),
                include_labels=[],
                exclude_labels=[],
                neighbor_filter={},
            )


def test_save_explorer_state_without_domain_proximity_raises():
    """Clustering committed but domain_proximity not committed → guard fires."""
    with tempfile.TemporaryDirectory() as tmp_str:
        analysis = Path(tmp_str) / "analysis"
        analysis.mkdir()
        m = Manifest(
            analysis_folder=str(analysis),
            steps={
                "clustering": StepEntry(
                    completed_at="2026-05-18T10:00:00Z",
                    params={},
                    params_hash="sha256:c",
                    outputs={"labels_json": "04_clustering/labels.json"},
                ),
            },
        )
        save_manifest(m, analysis)
        with pytest.raises(RuntimeError, match="Domain Proximity"):
            save_explorer_state(
                analysis_folder=str(analysis),
                include_labels=[],
                exclude_labels=[],
                neighbor_filter={},
            )


def test_load_explorer_state_returns_none_when_missing():
    with tempfile.TemporaryDirectory() as tmp_str:
        assert load_explorer_state(tmp_str) is None
