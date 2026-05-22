"""Shape tests for the v7 ``usage_events`` table + composite indexes."""
from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_usage_events_columns(pg_session) -> None:
    cols = {r[0] for r in (await pg_session.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='usage_events'"
    ))).all()}
    assert cols >= {"id", "user_id", "kind", "value_json", "ts"}


@pytest.mark.asyncio
async def test_usage_events_indexes(pg_session) -> None:
    rows = (await pg_session.execute(text(
        "SELECT indexname FROM pg_indexes WHERE tablename='usage_events'"
    ))).all()
    names = {r[0] for r in rows}
    assert any("user_id" in n and "ts" in n for n in names)
    assert any("kind"    in n and "ts" in n for n in names)
