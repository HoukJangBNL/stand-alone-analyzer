"""Cognito-backed authentication + authorization dependency.

This package houses the token verifier (tokens.py), user upsert logic
(users.py), the get_current_user FastAPI dependency, and the dev-bypass
mechanism (dev_bypass.py).

The exported User dataclass is the domain surface: UUID id, email, singular
role (UserRole enum), email_verified bool, and cognito_sub. Routes import
this module's User and get_current_user, making the dependency drop-in
compatible with the pre-Cognito stub (same import path).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.deps import get_db_session
from flake_analysis.db.models.auth import UserRole

__all__ = ["User", "get_current_user"]


@dataclass(frozen=True)
class User:
    """Authenticated user domain object (returned by get_current_user)."""

    id: UUID
    email: str
    role: UserRole
    email_verified: bool
    cognito_sub: str


async def get_current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
    """Extract and verify bearer token, upsert user, return domain User.

    When SAA_AUTH_DEV_BYPASS=1, short-circuits to mint_dev_user().
    On any authentication failure, raises HTTPException(401).
    """
    # Dev bypass check
    if os.getenv("SAA_AUTH_DEV_BYPASS") == "1":
        from flake_analysis.api.auth.dev_bypass import mint_dev_user

        return mint_dev_user()

    # Extract bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:]  # Strip "Bearer "

    try:
        # Verify token
        from flake_analysis.api.auth.tokens import verify_id_token

        claims = await verify_id_token(token)

        # Upsert user
        from flake_analysis.api.auth.users import upsert_from_claims

        user_model = await upsert_from_claims(session, claims)

        # Map ORM -> domain
        return User(
            id=user_model.id,
            email=user_model.email or "",
            role=user_model.role,
            email_verified=user_model.email_verified_at is not None,
            cognito_sub=user_model.cognito_sub or "",
        )

    except Exception as e:
        # All failures (InvalidToken, DB errors, etc) → 401
        raise HTTPException(status_code=401, detail=f"Authentication failed: {e}") from e
