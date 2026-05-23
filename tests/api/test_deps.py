"""W10-B: explicit project/scan dependency resolution."""
from __future__ import annotations

import pytest

from flake_analysis.api.deps import get_active_analysis, get_manifest
from flake_analysis.db.models import Analysis

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_get_manifest_loads_per_scan(tmp_path, monkeypatch):
    """get_manifest(pid, sid) reads root/<pid>/<sid>/manifest.json."""
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    target = tmp_path / "p1" / "42"
    target.mkdir(parents=True)
    (target / "manifest.json").write_text(
        '{"version": 1, "analysis_folder": null, "raw_images_dir": null,'
        ' "annotations_path": null, "created_at": null,'
        ' "flake_core_version": null, "steps": {}}',
        encoding="utf-8",
    )

    manifest = await get_manifest(project_id="p1", scan_id=42)
    assert manifest.version == 1


@pytest.mark.asyncio
async def test_get_manifest_missing_file_returns_fresh(tmp_path, monkeypatch):
    """Missing manifest.json -> fresh Manifest, not error."""
    monkeypatch.setenv("SAA_ANALYSIS_ROOT", str(tmp_path))
    manifest = await get_manifest(project_id="p1", scan_id=99)
    assert manifest.steps == {}


@pytest.mark.asyncio
async def test_get_active_analysis_explicit_scan_id(pg_session, sample_scan_factory):
    """get_active_analysis(scan_id, session) returns the analysis with that scan_id."""
    from flake_analysis.db.models import Model

    scan = await sample_scan_factory()
    # Analysis.model_id is FK->models(id) RESTRICT, so insert a Model first.
    m = Model(name="t-w10b-model", base_model="sam2", s3_uri="s3://t/w10b")
    pg_session.add(m)
    await pg_session.flush()

    a = Analysis(
        scan_id=scan.id,
        model_id=m.id,
        amg_params={},
        link_distance_px=1.0,
        min_area_px=10,
    )
    pg_session.add(a)
    await pg_session.flush()

    got = await get_active_analysis(scan_id=scan.id, session=pg_session)
    assert got is not None
    assert got.scan_id == scan.id


@pytest.mark.asyncio
async def test_get_active_analysis_no_row_returns_none(pg_session):
    """No analysis for that scan_id -> None (not an error)."""
    got = await get_active_analysis(scan_id=999_999, session=pg_session)
    assert got is None
