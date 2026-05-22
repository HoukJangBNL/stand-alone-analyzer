"""Usage event emission service (W6.4.1).

Helper for writing usage_events rows from request handlers.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.auth import User
from flake_analysis.db.models import UsageEvent


async def emit(
    session: AsyncSession,
    user: User,
    kind: str,
    value: dict | None = None,
) -> UsageEvent:
    """Write a usage event row for the given user and kind.

    Args:
        session: Active async DB session (caller must commit)
        user: Authenticated user (domain User from flake_analysis.api.auth)
        kind: Event kind (login, logout, scan_run, etc.)
        value: Optional JSONB payload for event-specific data

    Returns:
        The inserted UsageEvent with id and ts populated
    """
    event = UsageEvent(
        user_id=user.id,
        kind=kind,
        value_json=value,
    )
    session.add(event)
    await session.flush()  # Populate id and ts from DB defaults
    await session.refresh(event)  # Ensure all columns are loaded
    return event
