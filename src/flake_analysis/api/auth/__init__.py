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

from dataclasses import dataclass
from uuid import UUID

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


async def get_current_user() -> User:
    """Placeholder dependency — will be implemented in W6.2.3."""
    raise NotImplementedError("W6.2.3")
