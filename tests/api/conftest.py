"""Shared pytest fixtures for tests/api/."""
import os
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from jose.utils import long_to_base64

# Re-export PG fixtures from tests/db/conftest.py so pytest.mark.pg tests
# under tests/api/ can use them. pytest only auto-discovers conftest along
# the rootdir->test_file path; tests/db/conftest.py is on a sibling branch.
from tests.db.conftest import (  # noqa: F401
    pg_session,
    pg_url,
    sample_analysis_factory,
)


@pytest.fixture(autouse=True)
def _reset_active_project():
    """Reset deps._active_project around each test (one-shot cache leaks otherwise)."""
    import flake_analysis.api.deps as deps
    deps._active_project = None
    yield
    deps._active_project = None


@pytest.fixture(autouse=True)
def _enable_dev_bypass(monkeypatch):
    """Enable dev bypass for all API tests unless explicitly disabled.

    Tests that need real Cognito auth can disable this by passing
    `_enable_dev_bypass=False` or by using the signed_token fixture.
    """
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "1")
    monkeypatch.setenv("SAA_ENV", "dev")


@pytest.fixture
def rsa_key():
    """Generate an RSA keypair for test token signing."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def signed_token(rsa_key, monkeypatch):
    """Generate a valid Cognito ID token signed with test key.

    Sets up the JWKS cache and env vars so verify_id_token will accept it.
    """
    from flake_analysis.api.auth.tokens import _JwksCache

    # Build test JWKS
    pub = rsa_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": "test-kid",
        "use": "sig",
        "alg": "RS256",
        "n": long_to_base64(pub.n).decode(),
        "e": long_to_base64(pub.e).decode(),
    }

    # Prime the cache
    cache = _JwksCache()
    cache.set([jwk])
    monkeypatch.setattr("flake_analysis.api.auth.tokens._jwks_cache", cache)

    # Set env vars
    monkeypatch.setenv("SAA_COGNITO_AUDIENCE", "test-client-id")
    monkeypatch.setenv("SAA_COGNITO_ISSUER", "https://test.example/pool")

    # Sign a valid token
    pem = rsa_key.private_bytes(
        encoding=__import__("cryptography").hazmat.primitives.serialization.Encoding.PEM,
        format=__import__("cryptography").hazmat.primitives.serialization.PrivateFormat.PKCS8,
        encryption_algorithm=__import__("cryptography").hazmat.primitives.serialization.NoEncryption(),
    )

    now = int(time.time())
    claims = {
        "sub": "test-user-1",
        "aud": "test-client-id",
        "iss": "https://test.example/pool",
        "token_use": "id",
        "email": "test@example.com",
        "email_verified": True,
        "exp": now + 3600,
        "iat": now,
    }

    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": "test-kid"})
