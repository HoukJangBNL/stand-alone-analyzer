"""Filesystem layout: per-scan analysis folders.

Layout: <SAA_ANALYSIS_ROOT>/<project_id>/<scan_id>/
                                                  ├── manifest.json
                                                  ├── 00_thumbnails/
                                                  ├── 01_background/
                                                  ├── 02_domain_stats/
                                                  ├── 03_selector/
                                                  ├── 04_clustering/
                                                  ├── 05_domain_proximity/
                                                  └── 06_explorer/

W10-B introduced the (project_id, scan_id) dimensions; pre-W10 callers
resolved everything through a process-global `_active_project` in
`api/deps.py` (now removed).
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

SUBDIRS = {
    "background":       "01_background",
    "thumbnails":       "00_thumbnails",
    "domain_stats":     "02_domain_stats",
    "selector":         "03_selector",
    "clustering":       "04_clustering",
    "domain_proximity": "05_domain_proximity",
    "explorer":         "06_explorer",
}

ARTIFACTS = {
    "background": ["background.npy"],
    "thumbnails": ["index.json"],
    "domain_stats": ["stats.npz"],
    "selector": ["selection.parquet"],
    "clustering": ["seed_groups.json", "gmm_model.pkl", "assignments.parquet", "labels.json"],
    "domain_proximity": ["distances.parquet", "flake_assignments.parquet"],
    "explorer": ["explorer_state.json"],
}


def analysis_folder(root: str | Path, project_id: str, scan_id: int) -> Path:
    """Return the per-scan analysis folder.

    `<root>/<project_id>/<scan_id>/` — created lazily by callers that
    write into it (manifest.save_manifest does the mkdir). Pure path
    composition, no IO here.
    """
    if not project_id:
        raise ValueError("project_id must be a non-empty string")
    if not isinstance(scan_id, int) or scan_id <= 0:
        raise ValueError(f"scan_id must be a positive int, got {scan_id!r}")
    return Path(root) / project_id / str(scan_id)


def manifest_path(root: str | Path, project_id: str, scan_id: int) -> Path:
    """Return the manifest.json path for a (project_id, scan_id) pair (D5)."""
    return analysis_folder(root, project_id, scan_id) / "manifest.json"


def step_dir(analysis_folder_path: str | Path, step: str) -> Path:
    """Return the directory path for a given pipeline step within an analysis_folder."""
    if step not in SUBDIRS:
        raise ValueError(f"unknown step: {step}")
    return Path(analysis_folder_path) / SUBDIRS[step]
