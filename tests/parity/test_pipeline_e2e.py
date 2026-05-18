"""End-to-end pipeline smoke. Runs all 6 steps + verifies outputs.

This is the main 'standalone is internally consistent' test. After plan v1
r8 voided the live Qpress↔standalone diff, this harness validates structural
consistency of the standalone outputs against the synthetic fixture in
``fixture_builder.build_fixture``.

Per plan v1 r9 §M3.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tests.parity.fixture_builder import build_fixture

from flake_analysis.pipeline.background import run_background_step
from flake_analysis.pipeline.clustering import run_clustering_step
from flake_analysis.pipeline.domain_proximity import run_domain_proximity_step
from flake_analysis.pipeline.domain_stats import run_domain_stats_step
from flake_analysis.pipeline.explorer import save_explorer_state
from flake_analysis.pipeline.selector import run_selector_step
from flake_analysis.state.manifest import load_manifest


@pytest.fixture
def e2e_fixture(tmp_path):
    """Build raw_images/ + segmentation/annotations.json under tmp_path."""
    raw_dir, ann_path = build_fixture(tmp_path, n_images=5, image_size=200, seed=0)
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    return {
        "raw_dir": str(raw_dir),
        "ann_path": str(ann_path),
        "analysis": str(analysis),
    }


def _seed_groups_from_flake_ids(flake_ids: list[int]) -> list[dict]:
    """Pick the first 2 and last 2 ids as two seed groups (need >=4 ids)."""
    return [
        {"name": "low", "domain_ids": flake_ids[:2]},
        {"name": "high", "domain_ids": flake_ids[-2:]},
    ]


def test_e2e_full_pipeline_writes_all_outputs(e2e_fixture):
    """All six pipeline steps produce their expected output files."""
    af = e2e_fixture["analysis"]
    raw = e2e_fixture["raw_dir"]
    ann = e2e_fixture["ann_path"]

    # 1. Background
    run_background_step(
        raw_images_dir=raw, analysis_folder=af, seed=0, max_images=5,
    )
    assert (Path(af) / "01_background" / "background.npy").exists()

    # 2. Domain Stats
    run_domain_stats_step(
        raw_images_dir=raw, annotations_path=ann, analysis_folder=af,
        repr_mode="median",
    )
    assert (Path(af) / "02_domain_stats" / "stats.npz").exists()

    # 3. Domain Proximity
    run_domain_proximity_step(
        annotations_path=ann, analysis_folder=af,
        min_area_px=10, d_touch_px=2.0,
        link_distance_um=1.0, pixel_size_um=0.5, workers=1,
    )
    dp = Path(af) / "05_domain_proximity"
    assert (dp / "distances.parquet").exists()
    assert (dp / "flake_assignments.parquet").exists()

    # 4. Selector (no bounds = pass all)
    run_selector_step(analysis_folder=af)
    assert (Path(af) / "03_selector" / "selection.parquet").exists()

    # 5. Clustering
    npz = np.load(Path(af) / "02_domain_stats" / "stats.npz")
    flake_ids = npz["flake_ids"].astype(int).tolist()
    if len(flake_ids) < 4:
        pytest.skip(f"fixture only produced {len(flake_ids)} domains, need >=4 for 2-group GMM")

    run_clustering_step(
        analysis_folder=af,
        seed_groups=_seed_groups_from_flake_ids(flake_ids),
    )
    clu = Path(af) / "04_clustering"
    assert (clu / "labels.json").exists()
    assert (clu / "assignments.parquet").exists()
    assert (clu / "gmm_model.pkl").exists()
    assert (clu / "seed_groups.json").exists()

    # 6. Explorer state save
    save_explorer_state(
        analysis_folder=af,
        include_labels=["low"],
        exclude_labels=[],
        neighbor_filter={"size_enabled": False},
    )
    assert (Path(af) / "06_explorer" / "explorer_state.json").exists()

    # Manifest has all 6 steps completed.
    m = load_manifest(af)
    for step in (
        "background", "domain_stats", "domain_proximity",
        "selector", "clustering", "explorer",
    ):
        assert step in m.steps, f"manifest missing {step}"
        assert m.steps[step].completed_at is not None, f"{step} not completed"


def test_e2e_output_files_are_non_empty(e2e_fixture):
    """No 0-byte parquets / NPZs / JSONs allowed."""
    af = e2e_fixture["analysis"]
    raw = e2e_fixture["raw_dir"]
    ann = e2e_fixture["ann_path"]

    run_background_step(raw_images_dir=raw, analysis_folder=af, seed=0, max_images=5)
    run_domain_stats_step(raw_images_dir=raw, annotations_path=ann, analysis_folder=af)
    run_domain_proximity_step(
        annotations_path=ann, analysis_folder=af,
        min_area_px=10, link_distance_um=1.0, pixel_size_um=0.5, workers=1,
    )
    run_selector_step(analysis_folder=af)

    npz = np.load(Path(af) / "02_domain_stats" / "stats.npz")
    flake_ids = npz["flake_ids"].astype(int).tolist()
    if len(flake_ids) < 4:
        pytest.skip("not enough domains for clustering")

    run_clustering_step(
        analysis_folder=af,
        seed_groups=_seed_groups_from_flake_ids(flake_ids),
    )
    save_explorer_state(
        analysis_folder=af, include_labels=[], exclude_labels=[],
        neighbor_filter={"size_enabled": False},
    )

    expected = [
        "01_background/background.npy",
        "02_domain_stats/stats.npz",
        "03_selector/selection.parquet",
        "04_clustering/labels.json",
        "04_clustering/assignments.parquet",
        "04_clustering/gmm_model.pkl",
        "04_clustering/seed_groups.json",
        "05_domain_proximity/distances.parquet",
        "05_domain_proximity/flake_assignments.parquet",
        "06_explorer/explorer_state.json",
        "manifest.json",
    ]
    for rel in expected:
        p = Path(af) / rel
        assert p.exists(), f"missing: {rel}"
        assert p.stat().st_size > 0, f"empty file: {rel}"


def test_e2e_labels_json_conforms_to_schema(e2e_fixture):
    """labels.json frozen schema v1 (per plan §7.1)."""
    af = e2e_fixture["analysis"]
    raw = e2e_fixture["raw_dir"]
    ann = e2e_fixture["ann_path"]

    run_background_step(raw_images_dir=raw, analysis_folder=af, seed=0, max_images=5)
    run_domain_stats_step(raw_images_dir=raw, annotations_path=ann, analysis_folder=af)
    run_domain_proximity_step(
        annotations_path=ann, analysis_folder=af,
        min_area_px=10, link_distance_um=1.0, pixel_size_um=0.5, workers=1,
    )
    run_selector_step(analysis_folder=af)

    npz = np.load(Path(af) / "02_domain_stats" / "stats.npz")
    flake_ids = npz["flake_ids"].astype(int).tolist()
    if len(flake_ids) < 4:
        pytest.skip("not enough domains")
    run_clustering_step(
        analysis_folder=af,
        seed_groups=_seed_groups_from_flake_ids(flake_ids),
    )

    labels = json.loads((Path(af) / "04_clustering" / "labels.json").read_text())
    # Frozen schema v1 (plan §7.1)
    assert labels["version"] == 1
    assert isinstance(labels["n_clusters"], int)
    assert isinstance(labels["groups"], list)
    for g in labels["groups"]:
        assert {"id", "name", "size", "mean_rgb"} <= set(g)
        assert isinstance(g["mean_rgb"], list) and len(g["mean_rgb"]) == 3
    assert isinstance(labels["assignments"], dict)
    assert isinstance(labels["thresholds"], dict)
    assert labels["noise_label"] == -1
    assert labels["random_state"] == 42
    assert "fitted_at" in labels
