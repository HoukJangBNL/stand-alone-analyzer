"""Unit tests for async user upsert by cognito_sub (W6.2.2).

The upsert performs INSERT ... ON CONFLICT (cognito_sub) DO UPDATE,
ensuring idempotent first-login → member, and email changes are reflected.
"""
from __future__ import annotations

import pytest

from flake_analysis.api.auth.users import upsert_from_claims

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_first_login_creates_member(pg_session):
    """First login creates a new user with role=member."""
    u = await upsert_from_claims(
        pg_session,
        {
            "sub": "cog-1",
            "email": "a@b",
            "email_verified": True,
        },
    )
    assert u.role.value == "member"
    assert u.cognito_sub == "cog-1"
    assert u.email == "a@b"
    assert u.email_verified_at is not None


@pytest.mark.asyncio
async def test_second_login_is_idempotent(pg_session):
    """Second login with same cognito_sub returns the same user id."""
    a = await upsert_from_claims(
        pg_session,
        {"sub": "cog-1", "email": "a@b", "email_verified": True},
    )
    b = await upsert_from_claims(
        pg_session,
        {"sub": "cog-1", "email": "a@b", "email_verified": True},
    )
    assert a.id == b.id


@pytest.mark.asyncio
async def test_email_change_updates_row(pg_session):
    """Email change on subsequent login updates the email column."""
    a = await upsert_from_claims(
        pg_session,
        {"sub": "cog-1", "email": "a@b", "email_verified": True},
    )
    b = await upsert_from_claims(
        pg_session,
        {"sub": "cog-1", "email": "c@d", "email_verified": True},
    )
    assert a.id == b.id
    assert b.email == "c@d"


@pytest.mark.asyncio
async def test_email_unverified_clears_timestamp(pg_session):
    """email_verified=False clears email_verified_at."""
    a = await upsert_from_claims(
        pg_session,
        {"sub": "cog-1", "email": "a@b", "email_verified": True},
    )
    assert a.email_verified_at is not None

    b = await upsert_from_claims(
        pg_session,
        {"sub": "cog-1", "email": "a@b", "email_verified": False},
    )
    assert b.id == a.id
    assert b.email_verified_at is None
