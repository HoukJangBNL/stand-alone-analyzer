"""User upsert logic for cognito_sub-based identity (W6.2.2).

On token verification success, the claims dict is passed to upsert_from_claims
which performs INSERT ... ON CONFLICT (cognito_sub) DO UPDATE. First login
creates a new user with role=member; subsequent logins update email and
email_verified_at to mirror the latest Cognito state.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.db.models.auth import UserRole
from flake_analysis.db.models.user import User as UserModel


async def upsert_from_claims(
    session: AsyncSession, claims: dict[str, Any]
) -> UserModel:
    """Upsert user from Cognito ID token claims.

    Creates new user with role=member on first login. On subsequent logins,
    updates email and email_verified_at to mirror Cognito's current state.
    Returns the persisted User ORM instance.
    """
    cognito_sub = claims["sub"]
    email = claims.get("email")
    email_verified = claims.get("email_verified", False)
    email_verified_at = datetime.now(timezone.utc) if email_verified else None

    # Upsert using INSERT ... ON CONFLICT (cognito_sub) DO UPDATE
    stmt = (
        insert(UserModel)
        .values(
            cognito_sub=cognito_sub,
            email=email,
            email_verified_at=email_verified_at,
            role=UserRole.MEMBER,
        )
        .on_conflict_do_update(
            index_elements=["cognito_sub"],
            set_={
                "email": email,
                "email_verified_at": email_verified_at,
            },
        )
        .returning(UserModel.id)
    )

    result = await session.execute(stmt)
    user_id = result.scalar_one()
    await session.commit()

    # Fetch the full user to return
    select_stmt = select(UserModel).where(UserModel.id == user_id)
    result = await session.execute(select_stmt)
    return result.scalar_one()
