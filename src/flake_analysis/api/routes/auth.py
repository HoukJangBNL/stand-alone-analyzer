"""Authentication routes: /auth/me, /auth/callback, /auth/logout (W6.2.5)."""
from __future__ import annotations

import os
from typing import Annotated

import httpx
from fastapi import APIRouter, Body, Cookie, Depends, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_db_session

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthMeResponse(BaseModel):
    """Response schema for GET /auth/me."""

    id: str
    email: str
    role: str
    email_verified: bool
    cognito_sub: str


class AuthCallbackRequest(BaseModel):
    """Request schema for POST /auth/callback."""

    code: str
    redirect_uri: str


class AuthCallbackResponse(BaseModel):
    """Response schema for POST /auth/callback."""

    id_token: str
    expires_in: int
    user: AuthMeResponse


class AuthLogoutResponse(BaseModel):
    """Response schema for POST /auth/logout."""

    success: bool


@router.get("/me")
async def get_me(user: Annotated[User, Depends(get_current_user)]) -> AuthMeResponse:
    """Return the authenticated user's profile."""
    return AuthMeResponse(
        id=str(user.id),
        email=user.email,
        role=user.role.value,
        email_verified=user.email_verified,
        cognito_sub=user.cognito_sub,
    )


@router.post("/callback")
async def auth_callback(
    req: AuthCallbackRequest = Body(...),
    session: Annotated[AsyncSession, Depends(get_db_session)] = None,
) -> AuthCallbackResponse:
    """Exchange authorization code for tokens and set refresh cookie.

    Calls Cognito's oauth2/token endpoint with the authorization code.
    Returns the id_token and user profile, and sets a refresh token
    as an HttpOnly, Secure, SameSite=Lax cookie.

    Upserts the user in the DB and emits a login usage event.
    """
    domain = os.environ["SAA_COGNITO_HOSTED_UI_DOMAIN"]
    client_id = os.environ["SAA_COGNITO_APP_CLIENT_ID"]
    client_secret = os.environ["SAA_COGNITO_APP_CLIENT_SECRET"]

    token_url = f"{domain}/oauth2/token"

    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": req.code,
                "redirect_uri": req.redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_response.raise_for_status()
        tokens = token_response.json()

    # Decode ID token to get user info (no verification needed here since
    # the token came directly from Cognito's oauth2/token endpoint)
    from jose import jwt

    id_token_claims = jwt.get_unverified_claims(tokens["id_token"])

    # Upsert user in DB
    from flake_analysis.api.auth.users import upsert_from_claims

    user_model = await upsert_from_claims(session, id_token_claims)

    # Emit login usage event
    from flake_analysis.api.services.usage import emit
    from flake_analysis.db.models.auth import UserRole

    # Convert ORM user to domain User for emit
    domain_user = User(
        id=user_model.id,
        email=user_model.email,
        cognito_sub=user_model.cognito_sub,
        role=user_model.role,
        email_verified=user_model.email_verified_at is not None,
    )
    await emit(session, domain_user, "login")
    await session.commit()

    # Build user response
    user_data = AuthMeResponse(
        id=str(user_model.id),
        email=user_model.email,
        role=user_model.role.value,
        email_verified=user_model.email_verified_at is not None,
        cognito_sub=user_model.cognito_sub,
    )

    # Create response with refresh cookie
    response = Response(
        content=AuthCallbackResponse(
            id_token=tokens["id_token"],
            expires_in=tokens["expires_in"],
            user=user_data,
        ).model_dump_json(),
        media_type="application/json",
    )

    # Set refresh token as HttpOnly, Secure, SameSite=Lax cookie
    response.set_cookie(
        key="refresh",
        value=tokens["refresh_token"],
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=30 * 24 * 3600,  # 30 days
    )

    return response


@router.post("/logout")
async def auth_logout(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    refresh: Annotated[str | None, Cookie()] = None,
) -> AuthLogoutResponse:
    """Clear refresh cookie and call Cognito global sign-out.

    Clears the refresh cookie by setting Max-Age=0, and calls Cognito's
    global sign-out endpoint if a refresh token is present.

    Emits a logout usage event for the current user.
    """
    # Emit logout usage event
    from flake_analysis.api.services.usage import emit

    await emit(session, user, "logout")
    await session.commit()

    response = Response(
        content=AuthLogoutResponse(success=True).model_dump_json(),
        media_type="application/json",
    )

    # Clear the refresh cookie
    response.set_cookie(
        key="refresh",
        value="",
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=0,
    )

    # Call Cognito global sign-out if refresh token exists
    if refresh:
        domain = os.environ.get("SAA_COGNITO_HOSTED_UI_DOMAIN")
        client_id = os.environ.get("SAA_COGNITO_APP_CLIENT_ID")
        if domain and client_id:
            signout_url = f"{domain}/logout"
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        signout_url,
                        data={
                            "client_id": client_id,
                            "token": refresh,
                        },
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
            except Exception:
                # Best-effort sign-out; don't fail if Cognito is unreachable
                pass

    return response
