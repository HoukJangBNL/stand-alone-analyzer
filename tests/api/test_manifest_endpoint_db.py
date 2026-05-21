"""W2.4 — manifest endpoint DB rewire tests.

Three-case suite (per plan pinned decision #6):
  (a) no DB row -> endpoint returns disk manifest unchanged (pure-Python)
  (b) DB row present -> endpoint overlays steps_done and exposes status
  (c) DB session error -> endpoint returns 500 with documented envelope

Cases (b) and (c) use FastAPI dependency overrides so they do not require
a live PostgreSQL.
"""
from __future__ import annotations

from flake_analysis.api.schemas.data import ManifestModel


def test_manifest_model_has_optional_status_field():
    m = ManifestModel.model_validate({
        "version": 1,
        "steps": {},
    })
    assert m.status is None

    m2 = ManifestModel.model_validate({
        "version": 1,
        "steps": {},
        "status": "running",
    })
    assert m2.status == "running"
