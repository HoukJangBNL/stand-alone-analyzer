"""Route tests for /auth/me, /auth/callback, /auth/logout (W6.2.5)."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from flake_analysis.api.main import app


@pytest.mark.asyncio
@pytest.mark.pg
async def test_auth_me_returns_user(signed_token, monkeypatch, pg_session):
    """GET /auth/me with valid token returns user profile."""
    # Disable dev bypass for this test
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "0")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {signed_token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["email"] == "test@example.com"
        assert data["cognito_sub"] == "test-user-1"
        assert data["email_verified"] is True
        assert "id" in data
        assert "role" in data


@pytest.mark.asyncio
async def test_auth_me_rejects_missing_token(monkeypatch):
    """GET /auth/me without Authorization header returns 401."""
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "0")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/v1/auth/me")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_auth_callback_exchanges_code(monkeypatch, rsa_key):
    """POST /auth/callback exchanges code for tokens and sets refresh cookie."""
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
            "sub": "callback-test-user",
            "email": "callback@test.com",
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
        data = r.json()
        assert data["id_token"] == fake_id_token
        assert data["expires_in"] == 3600
        assert data["user"]["email"] == "callback@test.com"
        assert data["user"]["cognito_sub"] == "callback-test-user"
        # Check that refresh cookie is set
        cookies = r.cookies
        assert "refresh" in cookies
        refresh_cookie = cookies["refresh"]
        assert refresh_cookie == "fake-refresh-token"


@pytest.mark.asyncio
async def test_auth_logout_clears_refresh_cookie(monkeypatch):
    """POST /auth/logout clears refresh cookie and returns success."""
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "0")
    monkeypatch.setenv("SAA_COGNITO_HOSTED_UI_DOMAIN", "https://test.auth.example.com")
    monkeypatch.setenv("SAA_COGNITO_APP_CLIENT_ID", "test-client-id")

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
        # Set a refresh cookie first
        c.cookies.set("refresh", "some-refresh-token")

        r = await c.post("/api/v1/auth/logout")
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True

        # Check that refresh cookie is cleared (max-age=0)
        set_cookie_headers = r.headers.get_list("set-cookie")
        assert any("refresh=" in h and "Max-Age=0" in h for h in set_cookie_headers)
