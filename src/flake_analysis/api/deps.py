"""FastAPI dependencies (W10-B: per-scan, no globals).

Pre-W10 this module owned a process-global `_active_project` and a
`DEFAULT_PROJECT_ID = "local"` alias. W10-B retired both — every request
carries explicit `(project_id, scan_id)` from the path; the analysis
folder is `<SAA_ANALYSIS_ROOT>/<project_id>/<scan_id>/`.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.errors import DbUnavailable
from flake_analysis.db.engine import async_session_maker
from flake_analysis.db.models import Analysis
from flake_analysis.state.manifest import Manifest, load_manifest_for_scan


def _analysis_root() -> str:
    """Return the root directory under which all per-scan analysis folders live."""
    root = os.environ.get("SAA_ANALYSIS_ROOT")
    if root is None:
        # Backwards compat: legacy env var name from pre-W10
        root = os.environ.get("SAA_ANALYSIS_FOLDER", "/mnt/analysis")
    return root


async def get_manifest(project_id: str, scan_id: int) -> Manifest:
    """Load manifest.json for the (project_id, scan_id) pair (D5)."""
    return load_manifest_for_scan(_analysis_root(), project_id, scan_id)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield an async session per request; close on exit."""
    async with async_session_maker() as session:
        yield session


@asynccontextmanager
async def get_session_for_background():
    """Open a fresh session for code that runs in a background task /
    executor outside the request-scoped ``get_db_session`` lifetime.

    Caller must ``await session.commit()`` to persist. Used by SSE step
    routes (P2.6) to record `runs.completed_at` after the request scope
    has ended.
    """
    async with async_session_maker() as session:
        yield session


async def get_active_analysis(
    scan_id: int, session: AsyncSession
) -> Analysis | None:
    """Resolve the Analysis row for an explicit scan_id (D1).

    Per D1 the pipeline runs per scan; this function returns at most one
    analysis (the row whose scan_id matches). Pre-W10 silently fell back
    to `ORDER BY analyses.id DESC LIMIT 1` regardless of scan — that
    silent fallback is GONE. Returns ``None`` when no row exists; raises
    ``DbUnavailable`` (500) on SQL errors per pinned decision #5.
    """
    try:
        stmt = select(Analysis).where(Analysis.scan_id == scan_id).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise DbUnavailable() from exc
