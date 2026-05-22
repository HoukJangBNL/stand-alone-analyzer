"""v7 users-table column + enum + system-row checks.

Asserts that ``alembic upgrade head`` (run by the test environment, not by
this test) has produced a v7-shaped ``users`` table with a UUID primary key,
the new auth columns, the ``user_role`` ENUM, and a ``system`` row promoted
to ``role='admin'``.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_users_has_v7_columns(pg_session) -> None:
    rows = (await pg_session.execute(text(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = 'users' ORDER BY column_name"
    ))).all()
    cols = {r[0]: r[1] for r in rows}
    assert cols["id"] == "uuid"
    assert "cognito_sub" in cols
    assert "email" in cols
    assert "email_verified_at" in cols
    assert "organization" in cols
    assert "role" in cols
    assert "deactivated_at" in cols


@pytest.mark.asyncio
async def test_user_role_enum_values(pg_session) -> None:
    rows = (await pg_session.execute(text(
        "SELECT unnest(enum_range(NULL::user_role))::text"
    ))).all()
    assert {r[0] for r in rows} == {"member", "reader", "operator", "admin"}


@pytest.mark.asyncio
async def test_system_user_promoted_to_admin(pg_session) -> None:
    row = (await pg_session.execute(text(
        "SELECT role::text FROM users WHERE username = 'system'"
    ))).scalar_one()
    assert row == "admin"
