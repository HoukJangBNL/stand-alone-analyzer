"""Dev-bypass mechanism for local development (W6.2.4).

When SAA_AUTH_DEV_BYPASS=1, mint_dev_user() returns a stub admin user.
Module startup hard-fails if both SAA_AUTH_DEV_BYPASS=1 and SAA_ENV=prod.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.auth import User
from flake_analysis.db.models.auth import UserRole
from flake_analysis.db.models.user import User as UserModel

# Prod-leak guard: fail hard at module load time if dev-bypass leaks to prod
if os.getenv("SAA_AUTH_DEV_BYPASS") == "1" and os.getenv("SAA_ENV") == "prod":
    raise RuntimeError(
        "dev-bypass is enabled (SAA_AUTH_DEV_BYPASS=1) but SAA_ENV=prod. "
        "This is a security violation — dev-bypass must NEVER run in production."
    )


_DEV_USER_UUID = UUID("00000000-0000-0000-0000-000000000001")


async def ensure_dev_user_in_db(session: AsyncSession) -> None:
    """Idempotently insert the dev-bypass user row so FK constraints hold.

    Called from get_current_user when SAA_AUTH_DEV_BYPASS=1. Without this,
    any request that issues a usage_events insert (run/auth hooks) will
    violate the user_id FK because mint_dev_user's UUID has no row.
    """
    stmt = (
        pg_insert(UserModel)
        .values(
            id=_DEV_USER_UUID,
            email="local@dev",
            cognito_sub="dev:local",
            role=UserRole.ADMIN,
            email_verified_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    await session.execute(stmt)
    await session.commit()


def mint_dev_user() -> User:
    """Return stub admin user for dev bypass."""
    return User(
        id=_DEV_USER_UUID,
        email="local@dev",
        role=UserRole.ADMIN,
        email_verified=True,
        cognito_sub="dev:local",
    )
