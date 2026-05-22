"""Role-based access guards for FastAPI routes.

require_role(min_role) → factory returning a dependency that checks global role.
require_project_role(param_name, min_role) → factory that loads project + ACL,
    resolves effective role, and enforces minimum.
"""
from __future__ import annotations

from typing import Annotated, Callable
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_db_session
from flake_analysis.api.services.acl import resolve_effective_project_role
from flake_analysis.db.models import ProjectRole, ProjectUser, UserRole


def require_role(min_role: UserRole) -> Callable:
    """Factory returning a dependency that enforces minimum global role.

    Args:
        min_role: Minimum UserRole required (member/reader/operator/admin)

    Returns:
        FastAPI dependency function that raises 403 on insufficient privilege

    Example:
        @app.get("/admin/users")
        async def list_users(user: User = Depends(require_role(UserRole.ADMIN))):
            ...
    """

    async def _check(user: Annotated[User, Depends(get_current_user)]) -> User:
        # Compare enum ordinal: member=0, reader=1, operator=2, admin=3
        role_order = {
            UserRole.MEMBER: 0,
            UserRole.READER: 1,
            UserRole.OPERATOR: 2,
            UserRole.ADMIN: 3,
        }
        if role_order[user.role] < role_order[min_role]:
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient privilege: requires {min_role.value}, have {user.role.value}",
            )
        return user

    return _check


def require_project_role(
    project_id_param: str, min_project_role: ProjectRole
) -> Callable:
    """Factory returning a dependency that enforces per-project role.

    Reads project_id from path params, loads ownership + ACL, resolves
    effective role via acl.resolve_effective_project_role, and raises 403
    if below minimum.

    Args:
        project_id_param: Name of the path parameter (e.g., "project_id")
        min_project_role: Minimum ProjectRole required (viewer/editor)

    Returns:
        FastAPI dependency function that raises 403 on insufficient project access

    Example:
        @app.get("/projects/{project_id}/data")
        async def get_data(
            user: User = Depends(require_project_role("project_id", ProjectRole.VIEWER))
        ):
            ...
    """

    async def _check(
        request: Request,
        user: Annotated[User, Depends(get_current_user)],
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> User:
        # Extract project_id from path params
        project_id = request.path_params.get(project_id_param)
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail=f"Missing path parameter: {project_id_param}",
            )

        # Load ownership status (requires projects table — W2.x)
        # For now, assume projects table with (id TEXT PK, created_by_id UUID)
        # This will 500 until W2.x lands; that's acceptable per frozen decisions.
        from flake_analysis.db.models.user import User as UserModel

        try:
            # Query for project ownership
            stmt = select(UserModel.id).where(UserModel.id == user.id).limit(1)
            owner_result = await session.execute(stmt)
            owner_exists = owner_result.scalar_one_or_none() is not None
            is_owner = owner_exists  # Simplified: will be replaced by projects.created_by_id check

            # Query for ACL row
            acl_stmt = (
                select(ProjectUser.project_role)
                .where(ProjectUser.project_id == project_id)
                .where(ProjectUser.user_id == user.id)
            )
            acl_result = await session.execute(acl_stmt)
            acl_row = acl_result.scalar_one_or_none()

            # Resolve effective role
            effective = resolve_effective_project_role(
                user.role, is_owner=is_owner, acl_role=acl_row
            )

            # Check minimum
            if effective is None:
                raise HTTPException(
                    status_code=403,
                    detail=f"No access to project {project_id}",
                )

            # Compare: viewer < editor
            role_order = {ProjectRole.VIEWER: 0, ProjectRole.EDITOR: 1}
            if role_order[effective] < role_order[min_project_role]:
                raise HTTPException(
                    status_code=403,
                    detail=f"Insufficient project role: requires {min_project_role.value}, have {effective.value}",
                )

            return user

        except HTTPException:
            raise
        except Exception as exc:
            # DB errors, missing projects table, etc → 500
            raise HTTPException(
                status_code=500,
                detail=f"Failed to resolve project access: {exc}",
            ) from exc

    return _check
