"""Tests for usage.emit helper (W6.4.1)."""
from __future__ import annotations

import pytest
from sqlalchemy import select, text

from flake_analysis.api.services.usage import emit
from flake_analysis.db.models import UsageEvent

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_emit_writes_row(pg_session, sample_user_factory):
    """emit() writes a usage_events row with correct user_id, kind, and value_json."""
    u = await sample_user_factory()
    await emit(pg_session, u, "scan_run", {"analysis_id": 42})

    # Query back the row
    stmt = select(UsageEvent).where(UsageEvent.user_id == u.id)
    result = await pg_session.execute(stmt)
    row = result.scalar_one()

    assert row.kind == "scan_run"
    assert row.value_json == {"analysis_id": 42}
    assert row.user_id == u.id
    assert row.ts is not None


@pytest.mark.asyncio
async def test_emit_without_value_json(pg_session, sample_user_factory):
    """emit() with value=None writes an empty JSONB object."""
    u = await sample_user_factory()
    await emit(pg_session, u, "login")

    stmt = select(UsageEvent.value_json).where(UsageEvent.user_id == u.id)
    result = await pg_session.execute(stmt)
    value = result.scalar_one()

    # Default is {} from DB server_default
    assert value == {} or value is None


@pytest.mark.asyncio
async def test_emit_returns_event_with_id_and_ts(pg_session, sample_user_factory):
    """emit() returns the UsageEvent with id and ts populated."""
    u = await sample_user_factory()
    event = await emit(pg_session, u, "logout", {"reason": "test"})

    assert event.id is not None
    assert event.ts is not None
    assert event.kind == "logout"
    assert event.value_json == {"reason": "test"}
