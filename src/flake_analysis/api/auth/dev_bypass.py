"""Dev-bypass mechanism for local development (W6.2.4).

When SAA_AUTH_DEV_BYPASS=1, mint_dev_user() returns a stub admin user.
Module startup hard-fails if both SAA_AUTH_DEV_BYPASS=1 and SAA_ENV=prod.
"""
from __future__ import annotations

import os
from uuid import UUID

from flake_analysis.api.auth import User
from flake_analysis.db.models.auth import UserRole

# Prod-leak guard: fail hard at module load time if dev-bypass leaks to prod
if os.getenv("SAA_AUTH_DEV_BYPASS") == "1" and os.getenv("SAA_ENV") == "prod":
    raise RuntimeError(
        "dev-bypass is enabled (SAA_AUTH_DEV_BYPASS=1) but SAA_ENV=prod. "
        "This is a security violation — dev-bypass must NEVER run in production."
    )


def mint_dev_user() -> User:
    """Return stub admin user for dev bypass."""
    return User(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        email="local@dev",
        role=UserRole.ADMIN,
        email_verified=True,
        cognito_sub="dev:local",
    )
