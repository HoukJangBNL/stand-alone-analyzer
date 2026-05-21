"""Tests for db.reconcile — DB-derived manifest step status.

These are pure-Python tests; they construct lightweight stand-ins for
``Analysis`` without going through SQLAlchemy instrumentation (which
would require an active session / mapper context). The reconcile
helpers only read ``.steps_done`` and ``.status``, so a duck-typed
namespace is sufficient and avoids hitting the ORM machinery.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from flake_analysis.db.models import PipelineStatus
from flake_analysis.db.reconcile import (
    DB_TO_MANIFEST_STEP_MAP,
    analysis_status_string,
    derive_manifest_steps_from_analysis,
)


def _mk(steps_done: dict, status: PipelineStatus = PipelineStatus.PENDING) -> Any:
    """Build a duck-typed stand-in exposing the fields reconcile reads."""
    return SimpleNamespace(steps_done=steps_done, status=status)


def test_status_string_maps_each_enum_to_lowercase_value() -> None:
    assert analysis_status_string(_mk({}, PipelineStatus.PENDING)) == "pending"
    assert analysis_status_string(_mk({}, PipelineStatus.RUNNING)) == "running"
    assert analysis_status_string(_mk({}, PipelineStatus.COMPLETED)) == "completed"
    assert analysis_status_string(_mk({}, PipelineStatus.FAILED)) == "failed"


def test_derive_steps_empty_when_no_steps_done() -> None:
    a = _mk({}, PipelineStatus.PENDING)
    assert derive_manifest_steps_from_analysis(a) == {}


def test_derive_steps_only_truthy_keys_become_entries() -> None:
    a = _mk(
        {"background": True, "sam": True, "domain_stats": False},
        PipelineStatus.RUNNING,
    )
    out = derive_manifest_steps_from_analysis(a)
    # 'domain_stats' is False → skipped; 'failed' is reserved → never a step.
    assert set(out.keys()) == {"background", "sam"}
    for entry in out.values():
        assert entry["completed_at"] is None
        assert entry["params"] == {}
        assert entry["params_hash"] is None
        assert entry["input_hashes"] == {}
        assert entry["outputs"] == {}
        assert entry["reproducibility"] == {}


def test_derive_steps_ignores_failed_marker_key() -> None:
    a = _mk(
        {"background": True, "failed": "sam crashed"},
        PipelineStatus.FAILED,
    )
    out = derive_manifest_steps_from_analysis(a)
    assert "failed" not in out
    assert out.keys() == {"background"}


def test_derive_steps_ignores_unknown_keys() -> None:
    a = _mk({"background": True, "garbage": True}, PipelineStatus.RUNNING)
    out = derive_manifest_steps_from_analysis(a)
    assert "garbage" not in out
    assert out.keys() == {"background"}


def test_step_map_is_a_subset_of_pipeline_step_literal() -> None:
    """All DB step keys must be valid PipelineStep literals."""
    valid = {"background", "sam", "domain_stats", "domain_proximity"}
    assert set(DB_TO_MANIFEST_STEP_MAP.keys()) <= valid
