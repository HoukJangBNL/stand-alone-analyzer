"""Unit tests for the Cognito ID-token verifier (W6.2.1).

A test JWKS keypair is generated per test with ``cryptography`` so we never
talk to a real Cognito. Negative cases cover the full validation matrix.
"""
from __future__ import annotations

import time

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from jose.utils import long_to_base64

from flake_analysis.api.auth.tokens import (
    InvalidToken,
    _JwksCache,
    verify_id_token,
)

AUDIENCE = "client-abc"
ISSUER = "https://issuer.example/pool-1"


def _jwk_from_key(key, kid: str = "testkid") -> dict:
    pub = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": long_to_base64(pub.n).decode(),
        "e": long_to_base64(pub.e).decode(),
    }


def _sign(claims: dict, key, kid: str = "testkid") -> str:
    pem = key.private_bytes(
        encoding=__import__("cryptography").hazmat.primitives.serialization.Encoding.PEM,
        format=__import__("cryptography").hazmat.primitives.serialization.PrivateFormat.PKCS8,
        encryption_algorithm=__import__("cryptography").hazmat.primitives.serialization.NoEncryption(),
    )
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": kid})


def _base_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "sub": "user-1",
        "aud": AUDIENCE,
        "iss": ISSUER,
        "token_use": "id",
        "email": "u@e",
        "email_verified": True,
        "exp": now + 600,
        "iat": now,
    }
    claims.update(overrides)
    return claims


@pytest.fixture
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def rsa_key_alt():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(autouse=True)
def _cognito_env(monkeypatch):
    monkeypatch.setenv("SAA_COGNITO_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("SAA_COGNITO_ISSUER", ISSUER)


@pytest.fixture
def primed_cache(rsa_key, monkeypatch):
    cache = _JwksCache()
    cache.set([_jwk_from_key(rsa_key)])
    monkeypatch.setattr("flake_analysis.api.auth.tokens._jwks_cache", cache)
    return cache


@pytest.mark.asyncio
async def test_verify_id_token_happy(rsa_key, primed_cache):
    token = _sign(_base_claims(), rsa_key)
    claims = await verify_id_token(token)
    assert claims["sub"] == "user-1"
    assert claims["email_verified"] is True
    assert claims["email"] == "u@e"


@pytest.mark.asyncio
async def test_verify_id_token_expired(rsa_key, primed_cache):
    token = _sign(_base_claims(exp=int(time.time()) - 1), rsa_key)
    with pytest.raises(InvalidToken):
        await verify_id_token(token)


@pytest.mark.asyncio
async def test_verify_id_token_wrong_audience(rsa_key, primed_cache):
    token = _sign(_base_claims(aud="someone-else"), rsa_key)
    with pytest.raises(InvalidToken):
        await verify_id_token(token)


@pytest.mark.asyncio
async def test_verify_id_token_wrong_issuer(rsa_key, primed_cache):
    token = _sign(_base_claims(iss="https://evil.example/pool-1"), rsa_key)
    with pytest.raises(InvalidToken):
        await verify_id_token(token)


@pytest.mark.asyncio
async def test_verify_id_token_wrong_token_use(rsa_key, primed_cache):
    token = _sign(_base_claims(token_use="access"), rsa_key)
    with pytest.raises(InvalidToken):
        await verify_id_token(token)


@pytest.mark.asyncio
async def test_verify_id_token_missing_kid(rsa_key, monkeypatch, primed_cache):
    """A token whose ``kid`` is absent from JWKS (and refresh fails) is rejected."""

    async def _no_refresh():
        return []

    monkeypatch.setattr(
        "flake_analysis.api.auth.tokens._fetch_jwks",
        _no_refresh,
    )
    token = _sign(_base_claims(), rsa_key, kid="unknown-kid")
    with pytest.raises(InvalidToken):
        await verify_id_token(token)


@pytest.mark.asyncio
async def test_verify_id_token_malformed(primed_cache):
    with pytest.raises(InvalidToken):
        await verify_id_token("not.a.valid.jwt.at.all")


@pytest.mark.asyncio
async def test_verify_id_token_kid_rotation(rsa_key, rsa_key_alt, monkeypatch):
    """When a kid miss occurs, the cache refreshes and accepts the new key."""
    cache = _JwksCache()
    cache.set([_jwk_from_key(rsa_key, kid="old-kid")])
    monkeypatch.setattr("flake_analysis.api.auth.tokens._jwks_cache", cache)

    refresh_called = {"n": 0}

    async def _refresh():
        refresh_called["n"] += 1
        return [
            _jwk_from_key(rsa_key, kid="old-kid"),
            _jwk_from_key(rsa_key_alt, kid="new-kid"),
        ]

    monkeypatch.setattr("flake_analysis.api.auth.tokens._fetch_jwks", _refresh)

    token = _sign(_base_claims(), rsa_key_alt, kid="new-kid")
    claims = await verify_id_token(token)
    assert claims["sub"] == "user-1"
    assert refresh_called["n"] == 1


def test_jwks_cache_ttl_expiry(rsa_key):
    cache = _JwksCache(ttl_seconds=60)
    cache.set([_jwk_from_key(rsa_key, kid="k1")])
    assert cache.get_by_kid("k1") is not None
    cache._fetched_at = time.monotonic() - 120
    assert cache.get_by_kid("k1") is None
