"""Filesystem layout constants for analysis_folder/.

Per plan v1 r7 §6: 6 numbered subdirs.
"""
from __future__ import annotations
from pathlib import Path

PIPELINE_STEPS = (
    "background",
    "thumbnails",
    "domain_stats",
    "selector",
    "clustering",
    "domain_proximity",
    "explorer",
)

# Directory layout under analysis_folder/.
# ``00_thumbnails`` is added in v0.2.15: pre-rendered LOD pyramid for
# the Explorer substrate mosaic. Numbered 00 because it depends only
# on raw_images_dir (no other step) so it can run early — alongside or
# even before Background.
SUBDIRS = {
    "background":       "01_background",
    "thumbnails":       "00_thumbnails",
    "domain_stats":     "02_domain_stats",
    "selector":         "03_selector",
    "clustering":       "04_clustering",
    "domain_proximity": "05_domain_proximity",
    "explorer":         "06_explorer",
}

# Hardcoded artifact paths within each subdir (matches plan §6 + §7 r7)
ARTIFACTS = {
    "background": ["background.npy"],
    # Thumbnails: per-LOD subfolders (lod0/, lod1/, lod2/, lod3/) of
    # WebP thumbnails named after the raw image stem. ``index.json``
    # records the source raw_images_dir + file mtimes for cache hit
    # detection.
    "thumbnails": ["index.json"],
    "domain_stats": ["stats.npz"],
    "selector": ["selection.parquet"],
    "clustering": ["seed_groups.json", "gmm_model.pkl", "assignments.parquet", "labels.json"],
    "domain_proximity": ["distances.parquet", "flake_assignments.parquet"],
    "explorer": ["explorer_state.json"],
}


def step_dir(analysis_folder: str | Path, step: str) -> Path:
    """Return the directory path for a given pipeline step."""
    if step not in SUBDIRS:
        raise ValueError(f"unknown step: {step}")
    return Path(analysis_folder) / SUBDIRS[step]


def manifest_path(analysis_folder: str | Path) -> Path:
    return Path(analysis_folder) / "manifest.json"
