"""FastAPI dependencies per backend design §1."""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Annotated, AsyncIterator
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from flake_analysis.db.engine import async_session_maker
from flake_analysis.db.models import Analysis
from flake_analysis.state.manifest import Manifest, load_manifest

_active_project: str | None = None

DEFAULT_PROJECT_ID = "local"
DEFAULT_ANALYSIS_FOLDER = "/mnt/analysis"


@dataclass(frozen=True)
class ProjectContext:
    """Resolved project identity for a request.

    project_id is the opaque project handle (always "local" in v1).
    analysis_folder is the on-disk path that hosts the project's artifacts.
    """
    project_id: str
    analysis_folder: str


def _resolve_project_id(project_id: str) -> str:
    """Resolve project_id to analysis_folder path. v1: always returns _active_project."""
    global _active_project
    if _active_project is None:
        _active_project = os.environ.get("SAA_ANALYSIS_FOLDER", DEFAULT_ANALYSIS_FOLDER)
    return _active_project


async def get_project_context(request: Request) -> ProjectContext:
    """Resolve the active ProjectContext for this request.

    project_id is read from the path parameter named ``project_id`` when the
    route declares one (e.g. ``/projects/{project_id}/...``); otherwise it
    falls back to ``DEFAULT_PROJECT_ID``. analysis_folder is resolved through
    the same _active_project / SAA_ANALYSIS_FOLDER chain used by get_manifest.
    """
    pid = request.path_params.get("project_id", DEFAULT_PROJECT_ID)
    folder = _resolve_project_id(pid)
    return ProjectContext(project_id=pid, analysis_folder=folder)


async def get_manifest(project_id: str) -> Manifest:
    """Load manifest for project_id (v1: 'local')."""
    analysis_folder = _resolve_project_id(project_id)
    return load_manifest(analysis_folder)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Yield an async session per request; close on exit."""
    async with async_session_maker() as session:
        yield session


async def get_active_analysis(
    ctx: Annotated[ProjectContext, Depends(get_project_context)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Analysis | None:
    """Resolve the active Analysis row for the request's ProjectContext.

    v1 selects the most recent analyses row joined to scans whose project
    alias matches ctx.project_id. v1 always uses 'local' so this devolves
    to ``ORDER BY analyses.id DESC LIMIT 1`` — that is the v1 contract.
    Returns ``None`` when no row exists (silent fallback per pinned
    decision #1: clients without a DB-backed project keep their byte-for-byte
    disk-only response).
    """
    stmt = select(Analysis).order_by(Analysis.id.desc()).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
