"""W10-B: per-scan manifest IO."""
from __future__ import annotations

from pathlib import Path

import pytest

from flake_analysis.state.manifest import (
    Manifest,
    StepEntry,
    load_manifest,
    load_manifest_for_scan,
    save_manifest,
    save_manifest_for_scan,
)


def test_save_then_load_for_scan_round_trip(tmp_path):
    """save_manifest_for_scan writes; load_manifest_for_scan reads back."""
    m = Manifest(steps={"background": StepEntry(completed_at="2026-05-22T00:00:00Z")})
    save_manifest_for_scan(m, root=tmp_path, project_id="proj1", scan_id=7)

    loaded = load_manifest_for_scan(tmp_path, "proj1", 7)
    assert "background" in loaded.steps
    assert loaded.steps["background"].completed_at == "2026-05-22T00:00:00Z"


def test_load_for_scan_missing_returns_fresh(tmp_path):
    """No manifest.json yet → fresh Manifest, not error."""
    loaded = load_manifest_for_scan(tmp_path, "proj1", 99)
    assert loaded.steps == {}


def test_isolation_between_scans(tmp_path):
    """Two scans under same project don't see each other's manifests."""
    m1 = Manifest(steps={"background": StepEntry(completed_at="t1")})
    m2 = Manifest(steps={"selector": StepEntry(completed_at="t2")})
    save_manifest_for_scan(m1, root=tmp_path, project_id="p", scan_id=1)
    save_manifest_for_scan(m2, root=tmp_path, project_id="p", scan_id=2)

    a = load_manifest_for_scan(tmp_path, "p", 1)
    b = load_manifest_for_scan(tmp_path, "p", 2)
    assert "background" in a.steps and "selector" not in a.steps
    assert "selector" in b.steps and "background" not in b.steps
