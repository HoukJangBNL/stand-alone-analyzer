"""Projects CRUD (W10-C). Replaces the pre-W10 path-only stub."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api import errors as app_errors
from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_db_session
from flake_analysis.api.schemas.projects import (
    CreateProjectRequest,
    PatchProjectRequest,
    ProjectDetail,
    ProjectHandle,
    ProjectListResponse,
)
from flake_analysis.api.services import projects_service as svc

router = APIRouter(prefix="/projects", tags=["projects"])


def _to_handle(p) -> ProjectHandle:
    return ProjectHandle(
        project_id=p.id,
        name=p.name,
        owner_id=p.owner_id,
        description=p.description,
        created_at=p.created_at,
    )


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProjectListResponse:
    rows = await svc.list_projects_for_user(session, user_id=user.id)
    return ProjectListResponse(projects=[_to_handle(r) for r in rows])


@router.post(
    "",
    response_model=ProjectHandle,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    req: CreateProjectRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProjectHandle:
    try:
        p = await svc.create_project(
            session,
            owner_id=user.id,
            name=req.name,
            description=req.description,
        )
    except svc.DuplicateProjectName as exc:
        raise app_errors.DuplicateProjectName(name=str(exc)) from exc
    await session.commit()
    return _to_handle(p)


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(
    project_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProjectDetail:
    try:
        p, n = await svc.get_project_with_scan_count(
            session, project_id=project_id,
        )
    except svc.ProjectNotFound as exc:
        raise app_errors.ProjectNotFound(project_id=project_id) from exc
    handle = _to_handle(p)
    return ProjectDetail(scan_count=n, **handle.model_dump())


@router.patch("/{project_id}", response_model=ProjectHandle)
async def patch_project(
    project_id: str,
    req: PatchProjectRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProjectHandle:
    try:
        p = await svc.patch_project(
            session,
            project_id=project_id,
            name=req.name,
            description=req.description,
        )
    except svc.ProjectNotFound as exc:
        raise app_errors.ProjectNotFound(project_id=project_id) from exc
    except svc.DuplicateProjectName as exc:
        raise app_errors.DuplicateProjectName(name=str(exc)) from exc
    await session.commit()
    return _to_handle(p)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    try:
        await svc.delete_project_or_409(session, project_id=project_id)
    except svc.ProjectNotFound as exc:
        raise app_errors.ProjectNotFound(project_id=project_id) from exc
    except svc.ProjectHasScans as exc:
        raise app_errors.ProjectHasScans(
            project_id=project_id, scan_count=exc.scan_count
        ) from exc
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
