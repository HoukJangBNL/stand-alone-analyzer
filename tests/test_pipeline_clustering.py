"""Smoke tests for the Clustering pipeline wrapper.

Per plan v1 r9 §M2 PR 2.4 + §7.1 frozen labels.json schema.

The wrapper integrates with ``flake_core.pipeline.clustering.run_clustering``;
these tests build a synthetic 3-cluster fixture (visually distinct RGB
blobs) and verify:
  * All four output files are written
  * Manifest is updated with correct upstream input_hashes
  * labels.json frozen schema (version=1, noise_label=-1, random_state=42)
  * apply_thresholds rewrites threshold_pass without refitting
  * Mapping diagnostics count dropped seed ids
  * Prereq guards (no selector / no domain_stats)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from flake_analysis.pipeline.clustering import (
    apply_thresholds,
    run_clustering_step,
)
from flake_analysis.state.manifest import (
    Manifest,
    StepEntry,
    load_manifest,
    save_manifest,
)


def _setup_fixture(tmp: Path, n: int = 60):
    """Create stats.npz, selection.parquet, manifest with both prereqs done."""
    analysis = tmp / "analysis"
    (analysis / "02_domain_stats").mkdir(parents=True)
    (analysis / "03_selector").mkdir(parents=True)

    rng = np.random.default_rng(0)
    third = n // 3
    # 3 visually distinct blobs (low / mid / high luminance).
    rgb_a = rng.normal([60, 60, 60], 5, size=(third, 3))
    rgb_b = rng.normal([120, 120, 120], 5, size=(third, 3))
    rgb_c = rng.normal([200, 200, 200], 5, size=(n - 2 * third, 3))
    rgb = np.vstack([rgb_a, rgb_b, rgb_c]).clip(0, 255)

    flake_ids = np.arange(n, dtype=np.int64)
    np.savez(
        analysis / "02_domain_stats" / "stats.npz",
        repr_rgbs=rgb.astype(np.float64),
        std_pcts=rng.uniform(0, 30, size=(n, 3)).astype(np.float64),
        areas=rng.integers(50, 500, size=n).astype(np.float64),
        flake_ids=flake_ids,
        sam2=rng.uniform(0.5, 1.0, size=n).astype(np.float64),
    )

    sel_df = pd.DataFrame({
        "domain_id": flake_ids,
        "selected": [True] * n,
    })
    sel_df.to_parquet(
        analysis / "03_selector" / "selection.parquet",
        engine="pyarrow",
        index=False,
    )

    m = Manifest(
        analysis_folder=str(analysis),
        steps={
            "domain_stats": StepEntry(
                completed_at="2026-05-18T10:00:00Z",
                params={"min_area_px": 50, "repr_mode": "median"},
                params_hash="sha256:stats_abc",
                outputs={"stats_npz": "02_domain_stats/stats.npz"},
            ),
            "selector": StepEntry(
                completed_at="2026-05-18T10:05:00Z",
                params={"area_min": None, "area_max": None},
                params_hash="sha256:sel_abc",
                outputs={"selection_parquet": "03_selector/selection.parquet"},
            ),
        },
    )
    save_manifest(m, analysis)
    return analysis, flake_ids


def test_run_clustering_writes_all_outputs():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        analysis, flake_ids = _setup_fixture(tmp, n=60)

        seed_groups = [
            {"name": "low", "domain_ids": flake_ids[:5].tolist()},
            {"name": "mid", "domain_ids": flake_ids[20:25].tolist()},
            {"name": "high", "domain_ids": flake_ids[40:45].tolist()},
        ]

        result = run_clustering_step(
            analysis_folder=str(analysis),
            seed_groups=seed_groups,
        )

        # All four output files exist.
        clu = analysis / "04_clustering"
        assert (clu / "labels.json").exists()
        assert (clu / "assignments.parquet").exists()
        assert (clu / "gmm_model.pkl").exists()
        assert (clu / "seed_groups.json").exists()

        # seed_groups.json round-trips the user authoring verbatim.
        sg_disk = json.loads((clu / "seed_groups.json").read_text())
        assert [g["name"] for g in sg_disk] == ["low", "mid", "high"]
        assert sg_disk[0]["domain_ids"] == flake_ids[:5].tolist()

        # Manifest updated with upstream input_hashes.
        m = load_manifest(analysis)
        assert "clustering" in m.steps
        clu_entry = m.steps["clustering"]
        assert clu_entry.completed_at is not None
        assert clu_entry.input_hashes["domain_stats_params_hash"] == "sha256:stats_abc"
        assert clu_entry.input_hashes["selector_params_hash"] == "sha256:sel_abc"
        assert clu_entry.outputs["labels_json"] == "04_clustering/labels.json"
        assert clu_entry.outputs["seed_groups_json"] == "04_clustering/seed_groups.json"
        assert clu_entry.reproducibility.get("random_state") == 42

        # Diagnostic counters are present in the summary.
        assert "n_dropped_seed_ids" in result
        assert "n_dropped_selected_ids" in result
        # All ids were valid → zero dropped.
        assert result["n_dropped_seed_ids"] == 0
        assert result["n_dropped_selected_ids"] == 0


def test_run_clustering_without_selector_raises():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        analysis = tmp / "analysis"
        analysis.mkdir()
        with pytest.raises(RuntimeError, match="Domain Stats|Selector"):
            run_clustering_step(
                analysis_folder=str(analysis),
                seed_groups=[{"name": "x", "domain_ids": [0]}],
            )


def test_run_clustering_without_selector_committed_raises():
    """Domain stats committed but selector not committed → guard fires."""
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        analysis = tmp / "analysis"
        (analysis / "02_domain_stats").mkdir(parents=True)
        # Manifest with only domain_stats committed.
        m = Manifest(
            analysis_folder=str(analysis),
            steps={
                "domain_stats": StepEntry(
                    completed_at="2026-05-18T10:00:00Z",
                    params={},
                    params_hash="sha256:stats_only",
                    outputs={"stats_npz": "02_domain_stats/stats.npz"},
                ),
            },
        )
        save_manifest(m, analysis)
        with pytest.raises(RuntimeError, match="Selector"):
            run_clustering_step(
                analysis_folder=str(analysis),
                seed_groups=[{"name": "x", "domain_ids": [0]}],
            )


def test_run_clustering_labels_json_schema_v1():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        analysis, flake_ids = _setup_fixture(tmp, n=60)

        seed_groups = [
            {"name": "a", "domain_ids": flake_ids[:5].tolist()},
            {"name": "b", "domain_ids": flake_ids[20:25].tolist()},
        ]
        run_clustering_step(
            analysis_folder=str(analysis), seed_groups=seed_groups
        )

        labels = json.loads(
            (analysis / "04_clustering" / "labels.json").read_text()
        )
        # Frozen schema (plan §7.1)
        assert labels["version"] == 1
        assert "n_clusters" in labels
        assert "groups" in labels
        assert "assignments" in labels
        assert "thresholds" in labels
        assert "noise_label" in labels
        assert labels["noise_label"] == -1
        assert labels["random_state"] == 42
        assert "fitted_at" in labels
        # Each group has the canonical keys.
        for g in labels["groups"]:
            assert {"id", "name", "size", "mean_rgb"}.issubset(g.keys())
            assert isinstance(g["mean_rgb"], list) and len(g["mean_rgb"]) == 3


def test_apply_thresholds_updates_files():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        analysis, flake_ids = _setup_fixture(tmp, n=60)

        seed_groups = [
            {"name": "a", "domain_ids": flake_ids[:5].tolist()},
            {"name": "b", "domain_ids": flake_ids[20:25].tolist()},
        ]
        result = run_clustering_step(
            analysis_folder=str(analysis), seed_groups=seed_groups
        )
        n_clusters = int(result["n_clusters"])
        assert n_clusters >= 2

        # Apply tight thresholds — most points should fail.
        tight = {i: 0.99 for i in range(n_clusters)}
        summary_tight = apply_thresholds(
            analysis_folder=str(analysis),
            cluster_thresholds=tight,
        )
        assert summary_tight["n_total"] > 0
        assert summary_tight["n_clusters"] == n_clusters

        # Apply lax thresholds — most points should pass.
        lax = {i: 0.0 for i in range(n_clusters)}
        summary_lax = apply_thresholds(
            analysis_folder=str(analysis),
            cluster_thresholds=lax,
        )
        # Lax should never pass fewer than tight (monotone in threshold).
        assert summary_lax["n_pass"] >= summary_tight["n_pass"]

        # threshold_pass column written to assignments.parquet.
        df = pd.read_parquet(analysis / "04_clustering" / "assignments.parquet")
        assert "threshold_pass" in df.columns
        assert df["threshold_pass"].dtype == bool

        # labels.json thresholds field reflects the most recent apply.
        labels = json.loads(
            (analysis / "04_clustering" / "labels.json").read_text()
        )
        for cid in range(n_clusters):
            assert float(labels["thresholds"][str(cid)]) == 0.0

        # Manifest params.cluster_thresholds also reflects the most recent apply.
        m = load_manifest(analysis)
        thr_in_manifest = m.steps["clustering"].params.get("cluster_thresholds")
        assert thr_in_manifest is not None
        for cid in range(n_clusters):
            assert float(thr_in_manifest[str(cid)]) == 0.0


def test_apply_thresholds_without_commit_raises():
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        analysis = tmp / "analysis"
        analysis.mkdir()
        with pytest.raises(RuntimeError, match="not yet committed"):
            apply_thresholds(
                analysis_folder=str(analysis),
                cluster_thresholds={0: 0.5},
            )


def test_clustering_diagnostics_count_dropped_seed_ids():
    """Seed groups containing ids outside selector should be counted as dropped."""
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        analysis, flake_ids = _setup_fixture(tmp, n=60)

        # Group "a" has 3 valid ids + 2 fictional ids (9999, 10000).
        seed_groups = [
            {"name": "a", "domain_ids": [0, 1, 2, 9999, 10000]},
            {"name": "b", "domain_ids": [20, 21, 22]},
        ]
        result = run_clustering_step(
            analysis_folder=str(analysis), seed_groups=seed_groups
        )
        # The two fictional ids are not in the NPZ → core counts them as dropped seed ids.
        assert result["n_dropped_seed_ids"] >= 2
