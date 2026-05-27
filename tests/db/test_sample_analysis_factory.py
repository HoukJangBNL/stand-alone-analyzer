"""Regression tests for the ``sample_analysis_factory`` test fixture.

Issue #172: W10-A made ``scans.project_id`` NOT NULL FK→projects.id, but
``sample_analysis_factory`` used to construct ``Scan(name=..., material=...)``
without a project. The W10 acceptance gate patched two callers at the
fixture/test level (gate fix #2, commit fc173c4) but the factory itself
was left stale — any new test calling the factory with no overrides would
re-trigger the same NOT NULL violation.

These tests pin the contract: a no-arg call to the factory must produce
an Analysis whose Scan has a non-null ``project_id`` pointing at a real
``projects`` row.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from flake_analysis.db.models import Project, Scan

pytestmark = [pytest.mark.pg, pytest.mark.asyncio]


async def test_factory_default_wires_project_id(
    pg_session, sample_analysis_factory
):
    """No-arg factory call must produce a Scan with non-null project_id
    that resolves to an existing Project row.
    """
    analysis = await sample_analysis_factory()

    # Analysis is a real row.
    assert analysis.id is not None
    assert analysis.scan_id is not None

    # Scan carries a non-null project_id (the W10-A FK).
    scan = (
        await pg_session.execute(select(Scan).where(Scan.id == analysis.scan_id))
    ).scalar_one()
    assert scan.project_id is not None, (
        "sample_analysis_factory must wire scan.project_id (W10-A NOT NULL); "
        "see issue #172."
    )

    # project_id resolves to an actual projects row.
    project = (
        await pg_session.execute(
            select(Project).where(Project.id == scan.project_id)
        )
    ).scalar_one()
    assert project.owner_id is not None


async def test_factory_repeated_calls_are_independent(
    pg_session, sample_analysis_factory
):
    """Two no-arg calls produce two analyses with distinct Scans and
    distinct Projects (no shared-row coupling between calls).
    """
    a1 = await sample_analysis_factory()
    a2 = await sample_analysis_factory()

    assert a1.id != a2.id
    assert a1.scan_id != a2.scan_id

    rows = (
        await pg_session.execute(
            select(Scan.project_id).where(Scan.id.in_([a1.scan_id, a2.scan_id]))
        )
    ).all()
    project_ids = {row[0] for row in rows}
    assert len(project_ids) == 2, (
        "Each factory call should mint its own Project so tests stay isolated."
    )
    assert all(pid is not None for pid in project_ids)
