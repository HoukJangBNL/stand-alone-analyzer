"""Cognito ID token verifier with JWKS cache + TTL (W6.2.1).

Validates JWT signatures against Cognito's JWKS endpoint, enforcing:
- Signature validity (RS256 with correct KID)
- Expiration (exp claim)
- Audience (aud = SAA_COGNITO_AUDIENCE)
- Issuer (iss = SAA_COGNITO_ISSUER)
- Token type (token_use = 'id')

The JWKS cache refreshes on KID miss or TTL expiry (default 3600s).
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx
from jose import JWTError, jwt


class InvalidToken(Exception):
    """Token validation failed (expired, wrong aud/iss, bad signature, etc)."""


class _JwksCache:
    """In-memory JWKS cache with TTL and kid-based lookup."""

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._keys: dict[str, dict[str, Any]] = {}
        self._fetched_at: float = 0.0
        self.ttl_seconds = ttl_seconds

    def set(self, keys: list[dict[str, Any]]) -> None:
        """Replace the cache with fresh JWKS keys."""
        self._keys = {k["kid"]: k for k in keys}
        self._fetched_at = time.monotonic()

    def get_by_kid(self, kid: str) -> dict[str, Any] | None:
        """Retrieve key by kid if cache is fresh, else None (triggers refresh)."""
        if time.monotonic() - self._fetched_at > self.ttl_seconds:
            return None
        return self._keys.get(kid)


_jwks_cache = _JwksCache()


async def _fetch_jwks() -> list[dict[str, Any]]:
    """Fetch JWKS from Cognito's .well-known endpoint."""
    issuer = os.environ["SAA_COGNITO_ISSUER"]
    url = f"{issuer}/.well-known/jwks.json"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()["keys"]


async def verify_id_token(token: str) -> dict[str, Any]:
    """Verify Cognito ID token and return claims dict.

    Raises InvalidToken on any validation failure (expired, wrong aud/iss,
    bad signature, missing kid, token_use != 'id', malformed JWT).
    """
    audience = os.environ["SAA_COGNITO_AUDIENCE"]
    issuer = os.environ["SAA_COGNITO_ISSUER"]

    try:
        # Decode header to extract kid
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        if not kid:
            raise InvalidToken("Token header missing kid")

        # Try cached key, refresh on miss
        jwk = _jwks_cache.get_by_kid(kid)
        if jwk is None:
            keys = await _fetch_jwks()
            _jwks_cache.set(keys)
            jwk = _jwks_cache.get_by_kid(kid)
            if jwk is None:
                raise InvalidToken(f"kid {kid} not found in JWKS")

        # Verify signature + standard claims
        claims = jwt.decode(
            token,
            jwk,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer,
        )

        # Enforce token_use = 'id'
        if claims.get("token_use") != "id":
            raise InvalidToken(f"Expected token_use='id', got {claims.get('token_use')}")

        return claims

    except JWTError as e:
        raise InvalidToken(str(e)) from e
