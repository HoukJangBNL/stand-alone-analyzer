"""Shape tests for the v7 ``project_users`` ACL table."""
from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_project_users_table_shape(pg_session) -> None:
    cols = {r[0]: r[1] for r in (await pg_session.execute(text(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name='project_users'"
    ))).all()}
    assert cols["project_id"] == "text"
    assert cols["user_id"] == "uuid"
    assert "project_role" in cols
    assert "created_at" in cols


@pytest.mark.asyncio
async def test_project_users_pk_is_composite(pg_session) -> None:
    pk = (await pg_session.execute(text(
        "SELECT array_agg(a.attname ORDER BY a.attnum) "
        "FROM pg_index i JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) "
        "WHERE i.indrelid='project_users'::regclass AND i.indisprimary"
    ))).scalar_one()
    assert set(pk) == {"project_id", "user_id"}
