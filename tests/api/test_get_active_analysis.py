"""W2.4 — get_active_analysis dependency, PG-backed."""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.deps import ProjectContext, get_active_analysis

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_returns_none_when_no_analysis_row(pg_session: AsyncSession):
    ctx = ProjectContext(project_id="local", analysis_folder="/tmp")
    out = await get_active_analysis(ctx=ctx, session=pg_session)
    assert out is None


@pytest.mark.asyncio
async def test_returns_most_recent_analysis(pg_session, sample_analysis_factory):
    await sample_analysis_factory(steps_done={"background": True})
    newer = await sample_analysis_factory(
        steps_done={"background": True, "sam": True}
    )
    ctx = ProjectContext(project_id="local", analysis_folder="/tmp")
    out = await get_active_analysis(ctx=ctx, session=pg_session)
    assert out is not None
    assert out.id == newer.id
