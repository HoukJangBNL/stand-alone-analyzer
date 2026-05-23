"""W10 projects table ORM model.

`projects.id TEXT PK` (server-generated UUID v4 string) keeps wire-type
parity with the legacy `project_users.project_id TEXT` and
`scans.project_id TEXT` columns. See W10-A plan §"Plan-level decision"
for the TEXT-vs-UUID rationale.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flake_analysis.db.base import Base

if TYPE_CHECKING:
    from flake_analysis.db.models.auth import ProjectUser
    from flake_analysis.db.models.catalog import Scan


class Project(Base):
    """Top-level grouping for scans + ACL grants.

    Owner has implicit editor role via the W6.4 ACL resolver fast-path
    (no `project_users` row required when `owner_id == user.id`).
    """

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="projects_owner_name_uq"),
    )

    id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        server_default=text("gen_random_uuid()::text"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    scans: Mapped[list[Scan]] = relationship(back_populates="project")
    users: Mapped[list[ProjectUser]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
