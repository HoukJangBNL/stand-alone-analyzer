"""Merge DB-derived step status into a manifest-shaped dict.

Source of truth: ``analyses.steps_done`` for steps in
``DB_TO_MANIFEST_STEP_MAP``. Other steps stay disk-driven.

This module is pure: no I/O, no FastAPI imports, takes an Analysis row
already loaded by the caller's session.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any

from flake_analysis.db.reconcile import (
    analysis_status_string,
    derive_manifest_steps_from_analysis,
)

if TYPE_CHECKING:
    from flake_analysis.db.models import Analysis


def merge_db_steps_into_manifest(
    manifest_dict: dict[str, Any],
    analysis: "Analysis | None",
) -> dict[str, Any]:
    """Overlay DB-derived steps onto a manifest dict and add ``status``.

    Returns the same dict object when ``analysis is None`` (no defensive
    copy — caller already owns it). Mutates the dict otherwise.
    """
    if analysis is None:
        return manifest_dict
    db_steps = derive_manifest_steps_from_analysis(analysis)
    steps_out = dict(manifest_dict.get("steps") or {})
    steps_out.update(db_steps)  # DB wins for keys it covers.
    manifest_dict["steps"] = steps_out
    manifest_dict["status"] = analysis_status_string(analysis)
    return manifest_dict
