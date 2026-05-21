"""DB → manifest-shape reconciliation helpers.

Source-of-truth policy (post-cutover): ``analyses.steps_done`` (JSONB)
plus the GENERATED ``analyses.status`` ENUM are authoritative for
pipeline progress. The on-disk ``manifest.json`` is a per-project
filesystem artifact and may drift; do not write back to it from these
helpers.

These functions are intentionally pure: they take an ``Analysis`` row
(already loaded via the ORM) and emit dicts in the same shape as
``flake_analysis.api.schemas.data.StepEntryModel``. They do not perform
I/O and do not need a session.

Wiring into the live ``GET /projects/{pid}/data/manifest`` endpoint is
deferred to W2.1 (ProjectContext resolves project_id → analysis_id).
For now, callers that have an ``Analysis`` in hand can call these to
build a DB-derived view.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flake_analysis.db.models import Analysis


# Maps the JSON keys we store in analyses.steps_done to the manifest step
# names exposed by StepEntryModel. The set is closed: any key not in this
# map (including the reserved 'failed' marker) is ignored by the helper.
#
# IMPORTANT: keep these keys aligned with the runs.step CHECK constraint
# in alembic/versions/0001_initial_v6.py and with PipelineStep in
# src/flake_analysis/db/models/analysis.py. If you add a step there, add
# it here.
DB_TO_MANIFEST_STEP_MAP: dict[str, str] = {
    "background": "background",
    "sam": "sam",
    "domain_stats": "domain_stats",
    "domain_proximity": "domain_proximity",
}


def analysis_status_string(analysis: "Analysis") -> str:
    """Return the lowercase string form of analysis.status (e.g. 'running').

    Reading ``.status`` requires the row to have been loaded/refreshed
    after INSERT or UPDATE — see Analysis.status (Computed column).
    """
    return analysis.status.value


def derive_manifest_steps_from_analysis(
    analysis: "Analysis",
) -> dict[str, dict]:
    """Build a manifest-shaped ``steps`` dict from analyses.steps_done.

    Only keys present in ``DB_TO_MANIFEST_STEP_MAP`` with truthy values
    become entries. Each entry is a fresh ``StepEntryModel``-shaped dict
    with empty defaults — we do not have ``completed_at``, ``params``,
    or hashes in the v6 schema yet (they live in ``runs.metrics`` and
    are out of scope for this helper).
    """
    out: dict[str, dict] = {}
    steps_done = analysis.steps_done or {}
    for db_key, manifest_key in DB_TO_MANIFEST_STEP_MAP.items():
        if not steps_done.get(db_key):
            continue
        out[manifest_key] = {
            "completed_at": None,
            "params": {},
            "params_hash": None,
            "input_hashes": {},
            "outputs": {},
            "reproducibility": {},
        }
    return out
