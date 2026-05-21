"""W2.4 — pure merge helper for DB step_status overlay onto manifest dict."""
from __future__ import annotations

from flake_analysis.api.services.manifest_merge import merge_db_steps_into_manifest


def test_merge_with_none_analysis_passes_disk_through():
    disk = {"version": 1, "steps": {"background": {"completed_at": "2026-05-01T00:00Z"}}}
    out = merge_db_steps_into_manifest(disk, None)
    assert out is disk  # identity — no defensive copy needed when there's nothing to merge


def test_merge_with_analysis_overlays_db_steps_and_sets_status():
    class FakeAnalysis:
        steps_done = {"background": True, "sam": True, "domain_stats": False}

        class _Status:
            value = "running"

        status = _Status()

    disk = {
        "version": 1,
        "steps": {
            "background": {"completed_at": None, "params": {"a": 1}},
            "clustering": {"completed_at": "2026-05-01T00:00Z"},
        },
    }
    out = merge_db_steps_into_manifest(disk, FakeAnalysis())

    # DB-truthy steps appear/replace.
    assert "background" in out["steps"]
    assert "sam" in out["steps"]
    # DB-falsy steps do NOT add an entry.
    assert "domain_stats" not in out["steps"] or out["steps"].get("domain_stats", {}) == disk["steps"].get("domain_stats", {})
    # Disk-only steps survive.
    assert out["steps"]["clustering"]["completed_at"] == "2026-05-01T00:00Z"
    # Status copied through.
    assert out["status"] == "running"
