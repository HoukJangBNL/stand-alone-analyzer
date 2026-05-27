"""runs table audit-log helpers — shared by 4 SSE pipeline step routes.

Used by the background / sam / domain_stats / domain_proximity SSE step
routes (P2.6) to record per-step execution attempts in the ``runs``
table. The CHECK constraint on ``runs.step`` (see
``flake_analysis.db.models.analysis.Run.__table_args__``) restricts step
names to those four values; CPU-only steps (thumbnails, selector,
clustering, explorer) deliberately do NOT write here yet.

Status values follow ``PipelineStatus``: ``running`` on start,
``completed`` on success, ``failed`` on error. Note: the underlying
``pipeline_status`` enum uses ``completed`` (NOT ``succeeded``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.db.models.analysis import Run


async def record_run_start(
    session: AsyncSession,
    *,
    analysis_id: int,
    step: str,
    instance_meta: Optional[dict] = None,
) -> int:
    """Insert a 'running' Run row and return its primary key.

    Caller is responsible for committing (or letting the request-scoped
    session commit on success).
    """
    instance_meta = instance_meta or {}
    row = Run(
        analysis_id=analysis_id,
        step=step,
        status="running",
        started_at=datetime.now(timezone.utc),
        instance_type=instance_meta.get("instance_type"),
        instance_id=instance_meta.get("instance_id"),
        is_spot=instance_meta.get("is_spot"),
    )
    session.add(row)
    await session.flush()
    return row.id


async def record_run_end(
    session: AsyncSession,
    *,
    run_id: int,
    status: str,  # 'completed' | 'failed'
    error: Optional[str] = None,
    metrics: Optional[dict] = None,
) -> None:
    """Update Run.status / completed_at / error / metrics for an in-flight row.

    ``status`` must be one of ``"completed"`` or ``"failed"`` (matches
    ``PipelineStatus`` enum values).
    """
    await session.execute(
        update(Run)
        .where(Run.id == run_id)
        .values(
            status=status,
            completed_at=datetime.now(timezone.utc),
            error=error,
            metrics=metrics,
        )
    )
