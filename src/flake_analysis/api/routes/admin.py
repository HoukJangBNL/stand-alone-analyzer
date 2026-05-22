"""Admin-only routes for user role management, ACL, and deactivation.

All endpoints gated on require_role(UserRole.ADMIN).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.auth import User
from flake_analysis.api.deps import get_db_session
from flake_analysis.api.guards import require_role
from flake_analysis.db.models import ProjectRole, ProjectUser, UserRole
from flake_analysis.db.models.user import User as UserModel

router = APIRouter(tags=["admin"])


class ChangeRoleRequest(BaseModel):
    """Request body for changing user role."""

    role: UserRole


class ChangeRoleResponse(BaseModel):
    """Response for role change operation."""

    user_id: UUID
    role: UserRole


class GrantACLRequest(BaseModel):
    """Request body for granting project ACL."""

    user_id: UUID
    project_role: ProjectRole


class GrantACLResponse(BaseModel):
    """Response for ACL grant operation."""

    project_id: str
    user_id: UUID
    project_role: ProjectRole


class RevokeACLResponse(BaseModel):
    """Response for ACL revoke operation."""

    project_id: str
    user_id: UUID
    deleted: bool


class DeactivateResponse(BaseModel):
    """Response for deactivation operation."""

    user_id: UUID
    deactivated_at: datetime


class ReactivateResponse(BaseModel):
    """Response for reactivation operation."""

    user_id: UUID
    reactivated: bool


@router.post("/admin/users/{user_id}/role", response_model=ChangeRoleResponse)
async def change_user_role(
    user_id: UUID,
    req: ChangeRoleRequest,
    current_user: Annotated[User, Depends(require_role(UserRole.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ChangeRoleResponse:
    """Change a user's global role.

    Admin cannot demote themselves below admin.
    """
    # Prevent self-demotion
    if user_id == current_user.id and req.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=400,
            detail="Cannot demote yourself below admin",
        )

    # Update role
    stmt = (
        update(UserModel)
        .where(UserModel.id == user_id)
        .values(role=req.role)
        .returning(UserModel.id, UserModel.role)
    )
    result = await session.execute(stmt)
    row = result.one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    await session.commit()

    return ChangeRoleResponse(user_id=row.id, role=row.role)


@router.post("/admin/users/{user_id}/deactivate", response_model=DeactivateResponse)
async def deactivate_user(
    user_id: UUID,
    current_user: Annotated[User, Depends(require_role(UserRole.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DeactivateResponse:
    """Deactivate a user account.

    Admin cannot deactivate themselves.
    """
    # Prevent self-deactivation
    if user_id == current_user.id:
        raise HTTPException(
            status_code=400,
            detail="Cannot deactivate yourself",
        )

    now = datetime.now(timezone.utc)
    stmt = (
        update(UserModel)
        .where(UserModel.id == user_id)
        .values(deactivated_at=now)
        .returning(UserModel.id, UserModel.deactivated_at)
    )
    result = await session.execute(stmt)
    row = result.one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    await session.commit()

    return DeactivateResponse(user_id=row.id, deactivated_at=row.deactivated_at)


@router.post("/admin/users/{user_id}/reactivate", response_model=ReactivateResponse)
async def reactivate_user(
    user_id: UUID,
    current_user: Annotated[User, Depends(require_role(UserRole.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ReactivateResponse:
    """Reactivate a deactivated user account."""
    stmt = (
        update(UserModel)
        .where(UserModel.id == user_id)
        .values(deactivated_at=None)
        .returning(UserModel.id)
    )
    result = await session.execute(stmt)
    row = result.one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")

    await session.commit()

    return ReactivateResponse(user_id=row.id, reactivated=True)


@router.post("/admin/projects/{project_id}/acl", response_model=GrantACLResponse)
async def grant_project_acl(
    project_id: str,
    req: GrantACLRequest,
    current_user: Annotated[User, Depends(require_role(UserRole.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> GrantACLResponse:
    """Grant or update project ACL for a user.

    Upserts project_users row.
    """
    # Verify user exists
    stmt = select(UserModel.id).where(UserModel.id == req.user_id)
    result = await session.execute(stmt)
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=404, detail=f"User {req.user_id} not found"
        )

    # Upsert ACL
    # Check if exists
    acl_stmt = (
        select(ProjectUser)
        .where(ProjectUser.project_id == project_id)
        .where(ProjectUser.user_id == req.user_id)
    )
    acl_result = await session.execute(acl_stmt)
    existing = acl_result.scalar_one_or_none()

    if existing:
        # Update
        existing.project_role = req.project_role
    else:
        # Insert
        new_acl = ProjectUser(
            project_id=project_id,
            user_id=req.user_id,
            project_role=req.project_role,
        )
        session.add(new_acl)

    await session.commit()

    return GrantACLResponse(
        project_id=project_id,
        user_id=req.user_id,
        project_role=req.project_role,
    )


@router.delete(
    "/admin/projects/{project_id}/acl/{user_id}", response_model=RevokeACLResponse
)
async def revoke_project_acl(
    project_id: str,
    user_id: UUID,
    current_user: Annotated[User, Depends(require_role(UserRole.ADMIN))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> RevokeACLResponse:
    """Revoke project ACL for a user.

    Deletes project_users row. Idempotent (succeeds even if no row exists).
    """
    stmt = (
        delete(ProjectUser)
        .where(ProjectUser.project_id == project_id)
        .where(ProjectUser.user_id == user_id)
    )
    result = await session.execute(stmt)
    await session.commit()

    return RevokeACLResponse(
        project_id=project_id,
        user_id=user_id,
        deleted=result.rowcount > 0,
    )
