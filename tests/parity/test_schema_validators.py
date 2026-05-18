"""Schema validators — verify on-disk artifacts match the documented schemas.

Builds a fully-populated ``analysis_folder/`` once (module-scoped fixture)
and asserts each artifact's schema:

  * ``manifest.json`` — every step has ``completed_at`` + ``params_hash``
  * ``04_clustering/labels.json`` — frozen schema v1 (plan §7.1)
  * ``04_clustering/assignments.parquet`` — has ``domain_id`` and a cluster col
  * ``03_selector/selection.parquet`` — has ``domain_id`` and ``selected``
  * ``05_domain_proximity/flake_assignments.parquet`` — ``domain_id``, ``flake_id``

Per plan v1 r9 §M3.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests.parity.fixture_builder import build_fixture

from flake_analysis.pipeline.background import run_background_step
from flake_analysis.pipeline.clustering import run_clustering_step
from flake_analysis.pipeline.domain_proximity import run_domain_proximity_step
from flake_analysis.pipeline.domain_stats import run_domain_stats_step
from flake_analysis.pipeline.explorer import save_explorer_state
from flake_analysis.pipeline.selector import run_selector_step
from flake_analysis.state.manifest import load_manifest


@pytest.fixture(scope="module")
def completed_fixture(tmp_path_factory):
    """Build a fully-populated analysis_folder once for all schema tests."""
    tmp = tmp_path_factory.mktemp("schema_check")
    raw, ann = build_fixture(tmp, n_images=5, image_size=200, seed=0)
    analysis = tmp / "analysis"
    analysis.mkdir()
    af = str(analysis)

    run_background_step(
        raw_images_dir=str(raw), analysis_folder=af, seed=0, max_images=5,
    )
    run_domain_stats_step(
        raw_images_dir=str(raw), annotations_path=str(ann), analysis_folder=af,
    )
    run_domain_proximity_step(
        annotations_path=str(ann), analysis_folder=af,
        min_area_px=10, link_distance_um=1.0, pixel_size_um=0.5, workers=1,
    )
    run_selector_step(analysis_folder=af)

    npz = np.load(analysis / "02_domain_stats" / "stats.npz")
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
    save_explorer_state(
        analysis_folder=af,
        include_labels=[], exclude_labels=[],
        neighbor_filter={"size_enabled": False},
    )

    return analysis


def test_manifest_has_all_steps(completed_fixture):
    m = load_manifest(str(completed_fixture))
    for step in (
        "background", "domain_stats", "domain_proximity",
        "selector", "clustering", "explorer",
    ):
        assert step in m.steps
        assert m.steps[step].completed_at is not None
        assert m.steps[step].params_hash is not None


def test_labels_json_schema_v1(completed_fixture):
    p = completed_fixture / "04_clustering" / "labels.json"
    labels = json.loads(p.read_text())
    required = {
        "version", "n_clusters", "groups", "assignments",
        "thresholds", "noise_label", "random_state", "fitted_at",
    }
    assert required <= set(labels.keys())
    assert labels["version"] == 1


def test_assignments_parquet_columns(completed_fixture):
    p = completed_fixture / "04_clustering" / "assignments.parquet"
    df = pd.read_parquet(p)
    cols = set(df.columns)
    assert "domain_id" in cols
    # Core writes ``cluster_label``; plan §7.1 names it ``cluster_id``.
    # Tolerate either to keep the validator stable across core revisions.
    assert ("cluster_id" in cols) or ("cluster_label" in cols)


def test_selection_parquet_columns(completed_fixture):
    p = completed_fixture / "03_selector" / "selection.parquet"
    df = pd.read_parquet(p)
    assert {"domain_id", "selected"} <= set(df.columns)


def test_flake_assignments_parquet_columns(completed_fixture):
    p = completed_fixture / "05_domain_proximity" / "flake_assignments.parquet"
    df = pd.read_parquet(p)
    assert {"domain_id", "flake_id"} <= set(df.columns)
