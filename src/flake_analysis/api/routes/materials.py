"""W5-B1.2 — GET /materials, POST /materials."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from flake_analysis.api.auth import User, get_current_user
from flake_analysis.api.deps import get_db_session
from flake_analysis.api.schemas.upload import (
    MaterialCreateRequest,
    MaterialCreateResponse,
    MaterialItem,
    MaterialListResponse,
)
from flake_analysis.api.services import upload_service

router = APIRouter(prefix="/materials", tags=["materials"])


@router.get("")
async def list_materials(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MaterialListResponse:
    rows = await upload_service.list_materials(session)
    return MaterialListResponse(
        materials=[MaterialItem(name=r.name, created_at=r.created_at) for r in rows],
    )


@router.post("")
async def create_material(
    req: MaterialCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MaterialCreateResponse:
    if not req.name.strip():
        raise HTTPException(status_code=422, detail="material name is blank")
    canonical, created = await upload_service.upsert_material(
        session, name=req.name, created_by_id=user.id,
    )
    await session.commit()
    return MaterialCreateResponse(name=canonical, created=created)
