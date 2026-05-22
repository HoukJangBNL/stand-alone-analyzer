"""Usage event tests for /auth routes (W6.4.2)."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from flake_analysis.api.main import app
from flake_analysis.db.models import UsageEvent

pytestmark = pytest.mark.pg


@pytest.mark.asyncio
async def test_auth_callback_emits_login_event(monkeypatch, rsa_key, pg_session):
    """POST /auth/callback writes a usage_events row with kind='login'."""
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "0")
    monkeypatch.setenv("SAA_COGNITO_HOSTED_UI_DOMAIN", "https://test.auth.example.com")
    monkeypatch.setenv("SAA_COGNITO_APP_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("SAA_COGNITO_APP_CLIENT_SECRET", "test-secret")

    # Create a real JWT for the mock response
    from jose import jwt

    pem = rsa_key.private_bytes(
        encoding=__import__("cryptography").hazmat.primitives.serialization.Encoding.PEM,
        format=__import__("cryptography").hazmat.primitives.serialization.PrivateFormat.PKCS8,
        encryption_algorithm=__import__("cryptography").hazmat.primitives.serialization.NoEncryption(),
    )

    fake_id_token = jwt.encode(
        {
            "sub": "login-test-user",
            "email": "login@test.com",
            "email_verified": True,
            "aud": "test-client-id",
            "iss": "https://test.example/pool",
            "token_use": "id",
            "exp": int(time.time()) + 3600,
            "iat": int(time.time()),
        },
        pem,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )

    # Mock httpx.AsyncClient.post to avoid hitting real Cognito
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "id_token": fake_id_token,
        "access_token": "fake-access-token",
        "refresh_token": "fake-refresh-token",
        "expires_in": 3600,
        "token_type": "Bearer",
    }

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda: mock_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            "/api/v1/auth/callback",
            json={
                "code": "test-auth-code",
                "redirect_uri": "http://localhost:5173/auth/callback",
            },
        )
        assert r.status_code == 200

    # Check that a usage_events row was written with kind='login'
    stmt = select(UsageEvent).where(UsageEvent.kind == "login")
    result = await pg_session.execute(stmt)
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].kind == "login"


@pytest.mark.asyncio
async def test_auth_logout_emits_logout_event(monkeypatch, pg_session, sample_user_factory):
    """POST /auth/logout writes a usage_events row with kind='logout'."""
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "1")
    monkeypatch.setenv("SAA_COGNITO_HOSTED_UI_DOMAIN", "https://test.auth.example.com")
    monkeypatch.setenv("SAA_COGNITO_APP_CLIENT_ID", "test-client-id")

    # Create a user in the DB for dev bypass mode to pick up
    user = await sample_user_factory(email="logout@test.com", cognito_sub="logout-test-sub")

    # Mock httpx.AsyncClient.post for global sign-out call
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda: mock_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        # Set a refresh cookie
        c.cookies.set("refresh", "some-refresh-token")

        r = await c.post("/api/v1/auth/logout")
        assert r.status_code == 200

    # Check that a usage_events row was written with kind='logout'
    stmt = select(UsageEvent).where(UsageEvent.kind == "logout").where(UsageEvent.user_id == user.id)
    result = await pg_session.execute(stmt)
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].kind == "logout"
