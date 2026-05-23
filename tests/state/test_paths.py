"""W10-B: per-scan filesystem layout."""
from __future__ import annotations

from pathlib import Path

import pytest

from flake_analysis.state.paths import (
    analysis_folder,
    manifest_path,
    step_dir,
    SUBDIRS,
)


def test_analysis_folder_combines_root_project_scan(tmp_path):
    """analysis_folder(root, project_id, scan_id) -> root/project_id/scan_id/."""
    got = analysis_folder(tmp_path, "proj-abc", 42)
    assert got == tmp_path / "proj-abc" / "42"


def test_manifest_path_under_analysis_folder(tmp_path):
    """manifest_path is analysis_folder/manifest.json."""
    got = manifest_path(tmp_path, "proj-abc", 42)
    assert got == tmp_path / "proj-abc" / "42" / "manifest.json"


def test_step_dir_unchanged_takes_analysis_folder(tmp_path):
    """step_dir signature is unchanged — caller resolves analysis_folder first."""
    folder = analysis_folder(tmp_path, "p", 1)
    got = step_dir(folder, "background")
    assert got == folder / SUBDIRS["background"]


def test_analysis_folder_rejects_empty_project_id(tmp_path):
    with pytest.raises(ValueError):
        analysis_folder(tmp_path, "", 1)


def test_analysis_folder_rejects_non_positive_scan_id(tmp_path):
    with pytest.raises(ValueError):
        analysis_folder(tmp_path, "p", 0)
    with pytest.raises(ValueError):
        analysis_folder(tmp_path, "p", -1)
