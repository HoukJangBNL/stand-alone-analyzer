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


def _make_ten_blob_npz(path: Path) -> dict:
    """Ten well-separated RGB blobs of 10 domains each (100 total).

    Used to encode the owner-stated invariant for seed-driven clustering:
    only the seeded blobs become clusters; everything else is noise (-1).

    Layout: blob ``b`` owns ``domain_ids`` ``[10*b, 10*b + 10)``. Centers are
    chosen so all pairs are separated by >> the per-blob ``scale`` (so cluster
    identity is unambiguous), and ``np.random.default_rng(seed=11)`` is used
    so the fixture is deterministic. Seed picked because GMM seed-fit on 3
    members reproducibly converges with ``random_state=42`` for these blobs.
    """
    rng = np.random.default_rng(11)
    centers = np.array(
        [
            [30.0, 30.0, 30.0],
            [60.0, 30.0, 30.0],
            [30.0, 60.0, 30.0],
            [30.0, 30.0, 60.0],
            [200.0, 100.0, 100.0],
            [100.0, 200.0, 100.0],
            [100.0, 100.0, 200.0],
            [220.0, 220.0, 100.0],
            [100.0, 220.0, 220.0],
            [220.0, 100.0, 220.0],
        ],
        dtype=np.float64,
    )
    blobs = [rng.normal(loc=c, scale=2.0, size=(10, 3)) for c in centers]
    repr_rgbs = np.vstack(blobs).astype(np.float64)
    flake_ids = np.arange(100, dtype=np.int64)  # domain_ids 0..99
    np.savez(
        path,
        repr_rgbs=repr_rgbs,
        std_pcts=rng.uniform(0, 30, size=(100, 3)),
        areas=np.full(100, 500, dtype=np.int32),
        flake_ids=flake_ids,
    )
    return {"repr_rgbs": repr_rgbs, "flake_ids": flake_ids}


def test_run_clustering_leaves_ungrouped_bucket_for_unseeded_blobs():
    """W4.4 invariant: seeded blobs FULLY captured, unseeded blobs stay noise.

    Updated from W4.3 now that reg_covar=10.0 is the default — the
    rank-deficient seed covariance is regularised enough that all 10 members
    of a seeded blob fall inside the Mahalanobis gate, while unseeded blobs
    still sit beyond it. See claudedocs/clustering-tunable-spec.md §2c.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        npz_path = tmp / "stats.npz"
        sel_path = tmp / "selection.parquet"
        out_dir = tmp / "clustering_out"

        _make_ten_blob_npz(npz_path)
        _make_all_selected_parquet(sel_path, n=100)

        # Seed only 2 of the 10 blobs. Seeds use domain_ids — the wrapper
        # converts them to positional indices internally.
        seeded_blob_a = 0
        seeded_blob_b = 5
        seed_a_ids = [0, 1, 2]      # blob 0 owns ids 0..9
        seed_b_ids = [50, 51, 52]   # blob 5 owns ids 50..59
        seed_groups = [
            {"name": "graphite", "domain_ids": seed_a_ids},
            {"name": "h-bn", "domain_ids": seed_b_ids},
        ]

        result = run_clustering(
            npz_path,
            sel_path,
            seed_groups,
            output_dir=out_dir,
            rgb_threshold=0.5,
        )

        # ---- Output files exist ----------------------------------------
        assert (out_dir / "labels.json").exists()
        assert (out_dir / "assignments.parquet").exists()
        assert (out_dir / "gmm_model.pkl").exists()

        # ---- Invariant: only seeded groups become clusters -------------
        assert result["n_clusters"] == 2

        # ---- Invariant: ungrouped bucket is non-empty ------------------
        # This is the headline owner invariant: the pipeline must always
        # leave room for "not in any seeded group". Unseeded blobs land here.
        assert result["n_unassigned"] > 0

        # No mapping fallout in this clean fixture.
        assert result["n_dropped_seed_ids"] == 0
        assert result["n_dropped_selected_ids"] == 0
        assert result["params"]["random_state"] == 42

        # ---- Per-blob membership (W4.4 invariant) ----------------------
        adf = pd.read_parquet(out_dir / "assignments.parquet")
        assert {"domain_id", "cluster_label", "max_posterior"} <= set(adf.columns)
        labels = adf.set_index("domain_id")["cluster_label"]

        # Seeded blobs: ALL 10 members must share one non-negative label.
        seeded_labels = []
        for b in (seeded_blob_a, seeded_blob_b):
            rows = list(range(b * 10, b * 10 + 10))
            uniq = set(labels.loc[rows].tolist())
            assert len(uniq) == 1, (
                f"seeded blob {b} members must all share one label, got {uniq}"
            )
            lab = next(iter(uniq))
            assert lab >= 0, f"seeded blob {b} must be non-noise, got {lab}"
            seeded_labels.append(lab)
        assert seeded_labels[0] != seeded_labels[1]

        # Unseeded blobs: every member must be noise (-1).
        unseeded_blobs = [b for b in range(10) if b not in (seeded_blob_a, seeded_blob_b)]
        for b in unseeded_blobs:
            blob_ids = list(range(b * 10, b * 10 + 10))
            blob_labels = labels.loc[blob_ids].tolist()
            assert all(lab == -1 for lab in blob_labels), (
                f"unseeded blob {b} must be all noise, got {blob_labels}"
            )

        # ---- gmm_model.pkl round-trips ---------------------------------
        with open(out_dir / "gmm_model.pkl", "rb") as f:
            loaded = pickle.load(f)
        assert isinstance(loaded, InteractiveClusterResult)
        assert loaded.n_clusters == 2

        # ---- labels.json schema sanity (plan v1 r7 §7.1) ---------------
        labels_payload = json.loads((out_dir / "labels.json").read_text())
        assert labels_payload["version"] == 1
        assert labels_payload["n_clusters"] == 2
        assert len(labels_payload["groups"]) == 2
        assert labels_payload["noise_label"] == -1
        assert labels_payload["random_state"] == 42
        # ``assignments`` excludes noise per ``core/pipeline/clustering.py``
        # (the dict-comp at "if int(lab) >= 0"), so with ungrouped domains
        # present its size is strictly less than the 100 selected domains.
        assert len(labels_payload["assignments"]) < 100
        assert len(labels_payload["assignments"]) == result["n_assigned"]


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


def _run_two_blob(tmp: Path, **kwargs):
    npz_path = tmp / "stats.npz"
    sel_path = tmp / "selection.parquet"
    out_dir = tmp / "out"
    _make_two_blob_npz(npz_path)
    _make_all_selected_parquet(sel_path, n=100)
    seed_groups = [
        {"name": "dark", "domain_ids": [0, 1, 2]},
        {"name": "light", "domain_ids": [50, 51, 52]},
    ]
    return run_clustering(
        npz_path, sel_path, seed_groups,
        output_dir=out_dir, rgb_threshold=0.5, **kwargs,
    )


def test_run_clustering_accepts_reg_covar_and_records_in_params():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_two_blob(Path(tmp), reg_covar=2.5)
    assert result["params"]["reg_covar"] == 2.5


def test_run_clustering_default_reg_covar_is_ten():
    with tempfile.TemporaryDirectory() as tmp:
        result = _run_two_blob(Path(tmp))
    assert result["params"]["reg_covar"] == 10.0


def _make_overlap_fog_bench_npz(path: Path) -> dict:
    """10 Gaussian RGB blobs (with two overlapping pairs) + 100 fog points.

    Layout: blob b owns rows [10*b, 10*b+10); fog occupies rows 100..199.
    domain_ids 0..199 align with row order. Blobs (0,1) and (3,4) overlap.
    """
    rng = np.random.default_rng(23)
    centers = np.array([
        [40.0,  40.0,  40.0], [50.0,  40.0,  40.0],   # pair 1 overlaps
        [40.0,  60.0,  40.0],
        [200.0, 100.0, 100.0], [205.0, 105.0, 100.0], # pair 2 overlaps
        [100.0, 200.0, 100.0], [100.0, 100.0, 200.0],
        [220.0, 220.0, 100.0], [100.0, 220.0, 220.0], [220.0, 100.0, 220.0],
    ], dtype=np.float64)
    blobs = [rng.normal(loc=c, scale=2.0, size=(10, 3)) for c in centers]
    fog = rng.uniform(0.0, 255.0, size=(100, 3))
    repr_rgbs = np.vstack(blobs + [fog]).astype(np.float64)
    flake_ids = np.arange(200, dtype=np.int64)
    np.savez(
        path, repr_rgbs=repr_rgbs,
        std_pcts=rng.uniform(0, 30, size=(200, 3)),
        areas=np.full(200, 500, dtype=np.int32),
        flake_ids=flake_ids,
    )
    return {"repr_rgbs": repr_rgbs, "flake_ids": flake_ids}


def test_overlap_fog_bench_recall_and_leak_at_default_reg_covar():
    """At default reg_covar=10.0, max_mah=3.0: seeded recall >= 0.9, fog leak <= 0.05.

    Encodes the W4.4 baseline invariant on a tougher fixture (10 blobs, two
    overlapping pairs, 100 fog points). Seeded-blob recall measures the
    "seeded blobs FULLY captured" half; fog leak measures the "unseeded
    stays noise" half. Adjacent overlapping unseeded blobs are excluded from
    the leak budget because they share genuine RGB density with the seeded
    cluster — that bleed is by design and the auto-opt margin tiebreaker
    cannot remove it without dropping the seeded blob too.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        npz_path = tmp / "stats.npz"
        sel_path = tmp / "selection.parquet"
        out_dir = tmp / "out"
        _make_overlap_fog_bench_npz(npz_path)
        _make_all_selected_parquet(sel_path, n=200)
        # Seed blobs 0 and 5 with 3 members each.
        seed_groups = [
            {"name": "A", "domain_ids": [0, 1, 2]},
            {"name": "B", "domain_ids": [50, 51, 52]},
        ]
        # No reg_covar/max_mahalanobis kwargs — exercise the engine defaults.
        run_clustering(
            npz_path, sel_path, seed_groups,
            output_dir=out_dir, rgb_threshold=0.5,
        )
        adf = pd.read_parquet(out_dir / "assignments.parquet")
        labels = adf.set_index("domain_id")["cluster_label"]
        # Recall: seeded-blob rows that landed non-neg.
        seeded_rows = list(range(0, 10)) + list(range(50, 60))
        seeded_assigned = sum(int(labels.loc[i]) >= 0 for i in seeded_rows)
        recall = seeded_assigned / len(seeded_rows)
        assert recall >= 0.9, f"seeded blob recall {recall} < 0.9 at default reg_covar"
        # Leak: fog + non-overlapping unseeded blob rows. The overlapping
        # neighbours of seeded blobs (1 next to 0, 4 next to 3) are excluded
        # because their density physically overlaps the seeded cluster.
        non_overlapping_unseeded = [b for b in range(10) if b not in (0, 1, 5)]
        unseeded_blob_rows = [
            i for b in non_overlapping_unseeded for i in range(b * 10, b * 10 + 10)
        ]
        fog_rows = list(range(100, 200))
        non_seeded = unseeded_blob_rows + fog_rows
        leak = sum(int(labels.loc[i]) >= 0 for i in non_seeded) / len(non_seeded)
        assert leak <= 0.05, f"unseeded/fog leak {leak} > 0.05 at default reg_covar"


def test_run_clustering_step_records_reg_covar_in_manifest(tmp_path):
    """After run_clustering_step, manifest must record reg_covar."""
    from flake_analysis.pipeline.clustering import run_clustering_step
    from flake_analysis.state.manifest import (
        Manifest, StepEntry, load_manifest, save_manifest,
    )

    af = tmp_path / "analysis"
    (af / "02_domain_stats").mkdir(parents=True)
    (af / "03_selector").mkdir(parents=True)
    _make_two_blob_npz(af / "02_domain_stats" / "stats.npz")
    _make_all_selected_parquet(af / "03_selector" / "selection.parquet", n=100)

    # Stub upstream manifest entries that run_clustering_step gates on.
    stub = StepEntry(
        completed_at="2026-05-21T00:00:00Z",
        params={}, params_hash="sha256:0", input_hashes={},
        outputs={}, reproducibility={},
    )
    manifest = Manifest()
    manifest.steps["domain_stats"] = stub
    manifest.steps["selector"] = stub
    save_manifest(manifest, str(af))

    run_clustering_step(
        analysis_folder=str(af),
        seed_groups=[
            {"name": "dark", "domain_ids": [0, 1, 2]},
            {"name": "light", "domain_ids": [50, 51, 52]},
        ],
        reg_covar=2.0,
    )
    assert load_manifest(str(af)).steps["clustering"].params["reg_covar"] == 2.0
