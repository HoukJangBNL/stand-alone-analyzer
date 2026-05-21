"""Regenerate golden parity fixtures from the current commit.

This script runs the full standalone pipeline against the canonical
synthetic fixture (``fixture_builder.build_fixture(seed=0, n_images=5,
image_size=200)``) and writes authoritative output records into
``tests/parity/golden/``.

The records are consumed by ``tests/parity/test_golden_regression.py``
to detect numerical drift in ``flake_analysis.core``.

USAGE
-----
Run from the repo root with the venv active::

    python -m tests.parity.regenerate_golden

This will OVERWRITE the existing golden files. Only do this when the
algorithm has been intentionally updated AND the user has signed off
on the new numerical baseline.

WHAT IS STORED
--------------
For each pipeline output we store one of:

* ``sha256``: SHA-256 of the raw file bytes (for large or opaque
  binary blobs like ``background.npy`` and ``gmm_model.pkl``)
* ``columns``: per-column lists for parquet files (robust to pyarrow
  format changes)
* ``arrays``: per-key flat lists for the ``stats.npz`` arrays
* ``json``: a normalised JSON dump (volatile fields stripped) for
  ``labels.json``

See ``GOLDEN_SCHEMA_VERSION`` for any breaking record-format changes.

VOLATILE FIELDS
---------------
``labels.json["fitted_at"]`` is a wall-clock UTC timestamp written at
fit time and is stripped before comparison. Everything else in the
clustering output is reproducible across runs (verified empirically).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd

from flake_analysis.pipeline.background import run_background_step
from flake_analysis.pipeline.clustering import run_clustering_step
from flake_analysis.pipeline.domain_proximity import run_domain_proximity_step
from flake_analysis.pipeline.domain_stats import run_domain_stats_step
from flake_analysis.pipeline.selector import run_selector_step
from tests.parity.fixture_builder import build_fixture


GOLDEN_SCHEMA_VERSION = 1
GOLDEN_DIR = Path(__file__).parent / "golden"

# Volatile fields that must be stripped from JSON outputs before
# freezing or comparing (they change every run by design).
LABELS_VOLATILE_FIELDS = ("fitted_at",)


# ---------------------------------------------------------------------------
# Pipeline driver
# ---------------------------------------------------------------------------

def run_canonical_pipeline(workdir: Path) -> Path:
    """Run the full pipeline against the canonical fixture under workdir.

    Returns the analysis folder path.
    """
    raw, ann = build_fixture(workdir, n_images=5, image_size=200, seed=0)
    analysis = workdir / "analysis"
    analysis.mkdir()
    af = str(analysis)

    run_background_step(
        raw_images_dir=str(raw), analysis_folder=af, seed=0, max_images=5,
    )
    run_domain_stats_step(
        raw_images_dir=str(raw), annotations_path=str(ann), analysis_folder=af,
        repr_mode="median",
    )
    run_domain_proximity_step(
        annotations_path=str(ann), analysis_folder=af,
        min_area_px=10, d_touch_px=2.0,
        link_distance_um=1.0, pixel_size_um=0.5, workers=1,
    )
    run_selector_step(analysis_folder=af)

    npz = np.load(analysis / "02_domain_stats" / "stats.npz")
    flake_ids = npz["flake_ids"].astype(int).tolist()
    if len(flake_ids) < 4:
        raise RuntimeError(
            f"Canonical fixture produced only {len(flake_ids)} domains "
            "(need >=4 for the 2-group GMM). Fixture builder must have changed."
        )
    seed_groups = [
        {"name": "low",  "domain_ids": flake_ids[:2]},
        {"name": "high", "domain_ids": flake_ids[-2:]},
    ]
    run_clustering_step(analysis_folder=af, seed_groups=seed_groups)

    return analysis


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _array_record(arr: np.ndarray) -> Dict[str, Any]:
    """Build a serialisable record for a numpy array.

    Stores dtype, shape, and the raw values as a flat Python list so
    the JSON record is human-diffable. Floats are kept at full
    precision via ``repr`` round-trip in ``json.dumps``.
    """
    return {
        "dtype": str(arr.dtype),
        "shape": list(arr.shape),
        "values": arr.flatten().tolist(),
    }


def _dataframe_record(df: pd.DataFrame) -> Dict[str, Any]:
    """Build a serialisable record for a pandas DataFrame.

    Per-column lists, with explicit dtype tracking. Robust to changes
    in pyarrow's binary parquet format.
    """
    cols: Dict[str, Dict[str, Any]] = {}
    for c in df.columns:
        s = df[c]
        cols[c] = {
            "dtype": str(s.dtype),
            "values": s.tolist(),
        }
    return {
        "n_rows": int(len(df)),
        "columns": list(df.columns),
        "data": cols,
    }


def _strip_volatile(labels: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of labels.json with volatile fields removed."""
    cleaned = dict(labels)
    for f in LABELS_VOLATILE_FIELDS:
        cleaned.pop(f, None)
    return cleaned


def _array_diagnostics(arr: np.ndarray) -> Dict[str, Any]:
    """A few summary scalars for a large array — diagnostics only.

    Not used for assertions (the SHA-256 is the actual gate); these
    just make the golden file human-readable so a reviewer can spot
    whether a regression looks numerically tiny vs catastrophic.
    """
    flat = arr.astype(np.float64).flatten()
    return {
        "shape": list(arr.shape),
        "dtype": str(arr.dtype),
        "min": float(flat.min()),
        "max": float(flat.max()),
        "mean": float(flat.mean()),
        "std": float(flat.std()),
    }


def build_golden_records(analysis: Path) -> Dict[str, Any]:
    """Build the full golden record dict from a completed analysis folder."""
    bg_path = analysis / "01_background" / "background.npy"
    bg_arr = np.load(bg_path)

    stats_path = analysis / "02_domain_stats" / "stats.npz"
    npz = np.load(stats_path)

    sel_df = pd.read_parquet(analysis / "03_selector" / "selection.parquet")

    labels = json.loads((analysis / "04_clustering" / "labels.json").read_text())
    asn_df = pd.read_parquet(analysis / "04_clustering" / "assignments.parquet")
    pkl_path = analysis / "04_clustering" / "gmm_model.pkl"

    dist_df = pd.read_parquet(analysis / "05_domain_proximity" / "distances.parquet")
    flak_df = pd.read_parquet(analysis / "05_domain_proximity" / "flake_assignments.parquet")

    return {
        "schema_version": GOLDEN_SCHEMA_VERSION,
        "fixture": {
            "n_images": 5,
            "image_size": 200,
            "seed": 0,
            "blob_radius": 12,
        },
        "background": {
            "kind": "sha256_plus_diagnostics",
            "sha256": _sha256_file(bg_path),
            "diagnostics": _array_diagnostics(bg_arr),
        },
        "domain_stats": {
            "kind": "arrays",
            "arrays": {k: _array_record(npz[k]) for k in npz.files},
        },
        "selector": {
            "kind": "dataframe",
            "data": _dataframe_record(sel_df),
        },
        "clustering": {
            "labels": {
                "kind": "json_normalised",
                "stripped_fields": list(LABELS_VOLATILE_FIELDS),
                "data": _strip_volatile(labels),
            },
            "assignments": {
                "kind": "dataframe",
                "data": _dataframe_record(asn_df),
            },
            "gmm_model": {
                "kind": "sha256",
                "sha256": _sha256_file(pkl_path),
            },
        },
        "domain_proximity": {
            "distances": {
                "kind": "dataframe",
                "data": _dataframe_record(dist_df),
            },
            "flake_assignments": {
                "kind": "dataframe",
                "data": _dataframe_record(flak_df),
            },
        },
    }


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    import tempfile

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        analysis = run_canonical_pipeline(Path(tmp))
        records = build_golden_records(analysis)

    out = GOLDEN_DIR / "pipeline_golden.json"
    out.write_text(json.dumps(records, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
