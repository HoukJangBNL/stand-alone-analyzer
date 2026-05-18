"""Filesystem layout constants for analysis_folder/.

Per plan v1 r7 §6: 6 numbered subdirs.
"""
from __future__ import annotations
from pathlib import Path

PIPELINE_STEPS = (
    "background",
    "domain_stats",
    "selector",
    "clustering",
    "domain_proximity",
    "explorer",
)

# Directory layout under analysis_folder/ (matches plan §6)
SUBDIRS = {
    "background":       "01_background",
    "domain_stats":     "02_domain_stats",
    "selector":         "03_selector",
    "clustering":       "04_clustering",
    "domain_proximity": "05_domain_proximity",
    "explorer":         "06_explorer",
}

# Hardcoded artifact paths within each subdir (matches plan §6 + §7 r7)
ARTIFACTS = {
    "background": ["background.npy"],
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
