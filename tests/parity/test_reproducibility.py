"""Reproducibility — same fixture + same seeds produce identical outputs.

Tests two reproducibility properties:
  1. Background generation is byte-equal across runs (seed-controlled).
  2. Clustering produces identical n_clusters + assignments across runs
     (engine hard-codes random_state=42 per plan v1 r6 D6.1).

Per plan v1 r9 §M3.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple

import numpy as np
import pytest

from tests.parity.fixture_builder import build_fixture

from flake_analysis.pipeline.background import run_background_step
from flake_analysis.pipeline.clustering import run_clustering_step
from flake_analysis.pipeline.domain_proximity import run_domain_proximity_step
from flake_analysis.pipeline.domain_stats import run_domain_stats_step
from flake_analysis.pipeline.selector import run_selector_step


def _setup(tmp: Path, label: str) -> Tuple[str, str, str]:
    raw, ann = build_fixture(tmp / label, n_images=5, image_size=200, seed=0)
    analysis = tmp / f"{label}_analysis"
    analysis.mkdir()
    return str(raw), str(ann), str(analysis)


def test_background_seed_reproducibility(tmp_path):
    """Two builds with seed=0 → byte-equal background.npy."""
    raw_a, _, an_a = _setup(tmp_path, "a")
    raw_b, _, an_b = _setup(tmp_path, "b")

    run_background_step(raw_images_dir=raw_a, analysis_folder=an_a, seed=0, max_images=5)
    run_background_step(raw_images_dir=raw_b, analysis_folder=an_b, seed=0, max_images=5)

    bg_a = np.load(Path(an_a) / "01_background" / "background.npy")
    bg_b = np.load(Path(an_b) / "01_background" / "background.npy")
    np.testing.assert_array_equal(bg_a, bg_b)


def test_clustering_seeded_reproducibility(tmp_path):
    """Two full pipeline runs → identical n_clusters and assignments."""
    raw_a, ann_a, an_a = _setup(tmp_path, "a")
    raw_b, ann_b, an_b = _setup(tmp_path, "b")

    for raw, ann, af in [(raw_a, ann_a, an_a), (raw_b, ann_b, an_b)]:
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
            seed_groups=[
                {"name": "low",  "domain_ids": flake_ids[:2]},
                {"name": "high", "domain_ids": flake_ids[-2:]},
            ],
        )

    labels_a = json.loads((Path(an_a) / "04_clustering" / "labels.json").read_text())
    labels_b = json.loads((Path(an_b) / "04_clustering" / "labels.json").read_text())

    # Same seed groups + same data + random_state=42 → identical clustering result.
    assert labels_a["n_clusters"] == labels_b["n_clusters"]
    assert labels_a["assignments"] == labels_b["assignments"]
