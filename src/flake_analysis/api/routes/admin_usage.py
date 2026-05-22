"""Admin usage query route (W6.4.4).

GET /admin/usage with filters (user_id, kind, since, until, limit, aggregate).
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.auth import User
from flake_analysis.api.deps import get_db_session
from flake_analysis.api.guards import require_role
from flake_analysis.db.models import UsageEvent, UserRole

router = APIRouter(tags=["admin"])


class UsageEventResponse(BaseModel):
    """Single usage event."""

    id: int
    user_id: UUID
    kind: str
    value_json: Any | None
    ts: datetime


class UsageEventsResponse(BaseModel):
    """Response for GET /admin/usage (list mode)."""

    events: list[UsageEventResponse]


class UsageAggregateResponse(BaseModel):
    """Response for GET /admin/usage?aggregate=true."""

    counts_by_kind: dict[str, int]


@router.get("/admin/usage")
async def get_usage_events(
    current_user: Annotated[User, Depends(require_role(UserRole.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user_id: UUID | None = None,
    kind: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=100, le=1000),
    aggregate: bool = False,
) -> UsageEventsResponse | UsageAggregateResponse:
    """Query usage events with filters.

    Requires admin role. Supports filtering by user_id, kind, and time range.
    Returns events ordered by ts DESC (most recent first) unless aggregate=true.

    Args:
        user_id: Filter by user ID
        kind: Filter by event kind (login, logout, scan_run, etc.)
        since: Filter events >= this timestamp
        until: Filter events <= this timestamp
        limit: Maximum events to return (default 100, max 1000)
        aggregate: If true, return counts_by_kind instead of event list

    Returns:
        UsageEventsResponse with events list, or UsageAggregateResponse with counts
    """
    if aggregate:
        # Aggregate mode: count events grouped by kind
        stmt = select(UsageEvent.kind, func.count(UsageEvent.id).label("count"))

        # Apply filters
        if user_id:
            stmt = stmt.where(UsageEvent.user_id == user_id)
        if kind:
            stmt = stmt.where(UsageEvent.kind == kind)
        if since:
            stmt = stmt.where(UsageEvent.ts >= since)
        if until:
            stmt = stmt.where(UsageEvent.ts <= until)

        stmt = stmt.group_by(UsageEvent.kind)
        result = await session.execute(stmt)
        rows = result.all()

        counts_by_kind = {row.kind: row.count for row in rows}
        return UsageAggregateResponse(counts_by_kind=counts_by_kind)

    else:
        # List mode: return events ordered by ts DESC
        stmt = select(UsageEvent)

        # Apply filters
        if user_id:
            stmt = stmt.where(UsageEvent.user_id == user_id)
        if kind:
            stmt = stmt.where(UsageEvent.kind == kind)
        if since:
            stmt = stmt.where(UsageEvent.ts >= since)
        if until:
            stmt = stmt.where(UsageEvent.ts <= until)

        # Order by ts DESC (most recent first) and apply limit
        stmt = stmt.order_by(UsageEvent.ts.desc()).limit(limit)

        result = await session.execute(stmt)
        events = result.scalars().all()

        return UsageEventsResponse(
            events=[
                UsageEventResponse(
                    id=e.id,
                    user_id=e.user_id,
                    kind=e.kind,
                    value_json=e.value_json,
                    ts=e.ts,
                )
                for e in events
            ]
        )
