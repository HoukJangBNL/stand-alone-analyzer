"""Dev-bypass mechanism for local development (W6.2.4).

When SAA_AUTH_DEV_BYPASS=1, mint_dev_user() returns a stub admin user.
Module startup hard-fails if both SAA_AUTH_DEV_BYPASS=1 and SAA_ENV=prod.
"""
from __future__ import annotations

from uuid import UUID

from flake_analysis.api.auth import User
from flake_analysis.db.models.auth import UserRole


def mint_dev_user() -> User:
    """Return stub admin user for dev bypass (W6.2.4 will add prod guard)."""
    return User(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        email="local@dev",
        role=UserRole.ADMIN,
        email_verified=True,
        cognito_sub="dev:local",
    )
