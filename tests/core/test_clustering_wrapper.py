"""End-to-end test for the run_clustering wrapper (positional-index adapter).

Plan v1 r7 additions:
  * Diagnostic counters ``n_dropped_seed_ids`` / ``n_dropped_selected_ids``
    must surface in the result dict.
  * ``labels.json`` schema is frozen per plan §7.1.
"""
from __future__ import annotations

import json
import pickle
import re
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from flake_analysis.core.clustering import InteractiveClusterResult
from flake_analysis.core.pipeline import run_clustering


def _make_two_blob_npz(path: Path) -> dict:
    """Two well-separated RGB blobs of 50 domains each."""
    rng = np.random.default_rng(7)
    blob_a = rng.normal(loc=[50.0, 50.0, 50.0], scale=2.0, size=(50, 3))
    blob_b = rng.normal(loc=[200.0, 200.0, 200.0], scale=2.0, size=(50, 3))
    repr_rgbs = np.vstack([blob_a, blob_b]).astype(np.float64)
    flake_ids = np.arange(100, dtype=np.int64)  # domain_ids 0..99
    np.savez(
        path,
        repr_rgbs=repr_rgbs,
        std_pcts=rng.uniform(0, 30, size=(100, 3)),
        areas=np.full(100, 500, dtype=np.int32),
        flake_ids=flake_ids,
    )
    return {"repr_rgbs": repr_rgbs, "flake_ids": flake_ids}


def _make_all_selected_parquet(path: Path, n: int) -> None:
    df = pd.DataFrame(
        {"domain_id": np.arange(n, dtype=np.int64), "selected": [True] * n}
    )
    df.to_parquet(path, engine="pyarrow", index=False)


def test_run_clustering_two_blobs_separates_correctly():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        npz_path = tmp / "stats.npz"
        sel_path = tmp / "selection.parquet"
        out_dir = tmp / "clustering_out"

        _make_two_blob_npz(npz_path)
        _make_all_selected_parquet(sel_path, n=100)

        # Seeds use domain_ids — wrapper converts to positional indices.
        seed_groups = [
            {"name": "dark", "domain_ids": [0, 1, 2]},
            {"name": "light", "domain_ids": [50, 51, 52]},
        ]

        result = run_clustering(
            npz_path,
            sel_path,
            seed_groups,
            output_dir=out_dir,
            rgb_threshold=0.5,
        )

        # Output files exist.
        assert (out_dir / "labels.json").exists()
        assert (out_dir / "assignments.parquet").exists()
        assert (out_dir / "gmm_model.pkl").exists()

        # Two clusters, no unassigned given well-separated blobs at threshold 0.5.
        assert result["n_clusters"] == 2
        assert result["n_assigned"] == 100
        assert result["n_unassigned"] == 0

        # Random_state recorded in params.
        assert result["params"]["random_state"] == 42

        # Assignments match the blob structure.
        adf = pd.read_parquet(out_dir / "assignments.parquet")
        assert set(adf.columns) == {"domain_id", "cluster_label", "max_posterior"}
        labels = adf.set_index("domain_id")["cluster_label"]
        # All ids in [0, 50) share one label, [50, 100) share the other.
        first_half_labels = set(labels.loc[0:49].tolist())
        second_half_labels = set(labels.loc[50:99].tolist())
        assert len(first_half_labels) == 1
        assert len(second_half_labels) == 1
        assert first_half_labels != second_half_labels

        # gmm_model.pkl round-trips to InteractiveClusterResult.
        with open(out_dir / "gmm_model.pkl", "rb") as f:
            loaded = pickle.load(f)
        assert isinstance(loaded, InteractiveClusterResult)
        assert loaded.n_clusters == 2

        # labels.json shape sanity (plan v1 r7 §7.1 frozen schema).
        labels_payload = json.loads((out_dir / "labels.json").read_text())
        assert labels_payload["version"] == 1
        assert labels_payload["n_clusters"] == 2
        assert len(labels_payload["groups"]) == 2
        assert labels_payload["noise_label"] == -1
        assert labels_payload["random_state"] == 42
        # All 100 well-separated points should be assigned.
        assert len(labels_payload["assignments"]) == 100

        # No mapping fallout in the well-formed input.
        assert result["n_dropped_seed_ids"] == 0
        assert result["n_dropped_selected_ids"] == 0


def test_run_clustering_handles_partial_selection():
    """Selector keeps only some domain_ids; engine should run on the subset only."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        npz_path = tmp / "stats.npz"
        sel_path = tmp / "selection.parquet"
        out_dir = tmp / "clustering_out"

        _make_two_blob_npz(npz_path)
        # Keep only every other domain (50/100 selected), skewed toward both blobs.
        df = pd.DataFrame(
            {
                "domain_id": np.arange(100, dtype=np.int64),
                "selected": [i % 2 == 0 for i in range(100)],
            }
        )
        df.to_parquet(sel_path, engine="pyarrow", index=False)

        seed_groups = [
            {"name": "dark", "domain_ids": [0, 2, 4]},
            {"name": "light", "domain_ids": [50, 52, 54]},
        ]

        result = run_clustering(
            npz_path,
            sel_path,
            seed_groups,
            output_dir=out_dir,
            rgb_threshold=0.5,
        )

        assert result["n_clusters"] == 2
        adf = pd.read_parquet(out_dir / "assignments.parquet")
        assert len(adf) == 50  # Narrowed to selected subset.


def test_run_clustering_warns_on_seed_outside_selection():
    """Seed domain_ids outside the selected subset are skipped, not fatal."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        npz_path = tmp / "stats.npz"
        sel_path = tmp / "selection.parquet"
        out_dir = tmp / "clustering_out"

        _make_two_blob_npz(npz_path)
        # Selected: only domain_ids in [0, 60).
        df = pd.DataFrame(
            {
                "domain_id": np.arange(100, dtype=np.int64),
                "selected": [i < 60 for i in range(100)],
            }
        )
        df.to_parquet(sel_path, engine="pyarrow", index=False)

        # 70 and 71 are NOT selected — must be skipped, not raise.
        seed_groups = [
            {"name": "dark", "domain_ids": [0, 1, 2]},
            {"name": "light", "domain_ids": [50, 70, 71]},
        ]
        result = run_clustering(
            npz_path,
            sel_path,
            seed_groups,
            output_dir=out_dir,
            rgb_threshold=0.5,
        )
        assert result["n_clusters"] == 2
        # Plan v1 r7: dropped seed ids surface in the diagnostic counter.
        assert result["n_dropped_seed_ids"] == 2
        # No selected domain_id is missing from the NPZ in this fixture.
        assert result["n_dropped_selected_ids"] == 0


def test_run_clustering_diagnostic_counters_present():
    """Result dict carries r7 mapping-diagnostic counters even on the happy path."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        npz_path = tmp / "stats.npz"
        sel_path = tmp / "selection.parquet"
        out_dir = tmp / "clustering_out"

        _make_two_blob_npz(npz_path)
        _make_all_selected_parquet(sel_path, n=100)

        seed_groups = [
            {"name": "dark", "domain_ids": [0, 1, 2]},
            {"name": "light", "domain_ids": [50, 51, 52]},
        ]

        result = run_clustering(
            npz_path,
            sel_path,
            seed_groups,
            output_dir=out_dir,
            rgb_threshold=0.5,
        )
        assert "n_dropped_seed_ids" in result
        assert "n_dropped_selected_ids" in result
        assert result["n_dropped_seed_ids"] == 0
        assert result["n_dropped_selected_ids"] == 0


def test_run_clustering_counts_selected_ids_missing_from_npz():
    """Selected domain_ids absent from the stats NPZ are counted, not fatal."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        npz_path = tmp / "stats.npz"
        sel_path = tmp / "selection.parquet"
        out_dir = tmp / "clustering_out"

        _make_two_blob_npz(npz_path)  # NPZ has flake_ids 0..99 only.

        # Selector includes domain_ids 0..99 (all in NPZ) plus 200..204 (NOT in NPZ).
        all_ids = np.concatenate(
            [np.arange(100, dtype=np.int64), np.arange(200, 205, dtype=np.int64)]
        )
        df = pd.DataFrame({"domain_id": all_ids, "selected": [True] * len(all_ids)})
        df.to_parquet(sel_path, engine="pyarrow", index=False)

        seed_groups = [
            {"name": "dark", "domain_ids": [0, 1, 2]},
            {"name": "light", "domain_ids": [50, 51, 52]},
        ]
        result = run_clustering(
            npz_path,
            sel_path,
            seed_groups,
            output_dir=out_dir,
            rgb_threshold=0.5,
        )
        # 5 selected ids were not in the NPZ → counted, dropped, run continues.
        assert result["n_dropped_selected_ids"] == 5
        assert result["n_dropped_seed_ids"] == 0
        assert result["n_clusters"] == 2


def test_run_clustering_labels_json_schema():
    """labels.json conforms to the plan v1 r7 §7.1 frozen schema."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        npz_path = tmp / "stats.npz"
        sel_path = tmp / "selection.parquet"
        out_dir = tmp / "clustering_out"

        _make_two_blob_npz(npz_path)
        _make_all_selected_parquet(sel_path, n=100)

        seed_groups = [
            {"name": "graphite", "domain_ids": [0, 1, 2]},
            {"name": "h-bn", "domain_ids": [50, 51, 52]},
        ]
        run_clustering(
            npz_path,
            sel_path,
            seed_groups,
            output_dir=out_dir,
            rgb_threshold=0.5,
        )

        payload = json.loads((out_dir / "labels.json").read_text())

        # Top-level required keys.
        required = {
            "version",
            "n_clusters",
            "groups",
            "assignments",
            "thresholds",
            "noise_label",
            "random_state",
            "fitted_at",
        }
        assert required <= set(payload.keys()), (
            f"missing required top-level keys: {required - set(payload.keys())}"
        )

        # Field types and values.
        assert payload["version"] == 1
        assert payload["n_clusters"] == 2
        assert payload["noise_label"] == -1
        assert payload["random_state"] == 42

        # fitted_at: ISO 8601 UTC zulu format.
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", payload["fitted_at"])

        # groups: list of {id, name, size, mean_rgb}.
        assert isinstance(payload["groups"], list)
        assert len(payload["groups"]) == 2
        for grp in payload["groups"]:
            assert set(grp.keys()) == {"id", "name", "size", "mean_rgb"}
            assert isinstance(grp["id"], int)
            assert isinstance(grp["name"], str)
            assert isinstance(grp["size"], int)
            assert isinstance(grp["mean_rgb"], list)
            assert len(grp["mean_rgb"]) == 3
            assert all(isinstance(v, float) for v in grp["mean_rgb"])

        # Group names propagated from seed_groups.
        names = {g["name"] for g in payload["groups"]}
        assert names == {"graphite", "h-bn"}

        # assignments: dict[str(domain_id) -> int(cluster_label)].
        assert isinstance(payload["assignments"], dict)
        for k, v in payload["assignments"].items():
            assert isinstance(k, str) and k.lstrip("-").isdigit()
            assert isinstance(v, int) and v >= 0  # noise excluded from assignments

        # thresholds: dict[str(cluster_id) -> float].
        assert isinstance(payload["thresholds"], dict)
        assert set(payload["thresholds"].keys()) == {"0", "1"}
        for v in payload["thresholds"].values():
            assert isinstance(v, float)
