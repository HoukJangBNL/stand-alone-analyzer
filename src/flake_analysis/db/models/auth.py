"""v7 auth + ACL + usage_events ORM models.

Houses the global ``UserRole`` ENUM, the per-project ``ProjectRole`` ENUM,
the ``project_users`` ACL table, and the ``usage_events`` telemetry table.
``User`` itself lives in ``user.py`` — it imports ``UserRole`` from here.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flake_analysis.db.base import Base

if TYPE_CHECKING:
    from flake_analysis.db.models.projects import Project


class UserRole(str, Enum):
    """Global RBAC tier. Comparison order: member < reader < operator < admin."""

    MEMBER = "member"
    READER = "reader"
    OPERATOR = "operator"
    ADMIN = "admin"


class ProjectRole(str, Enum):
    """Per-project ACL grant. ``editor`` is strictly stronger than ``viewer``."""

    VIEWER = "viewer"
    EDITOR = "editor"


_project_role_enum = postgresql.ENUM(
    ProjectRole,
    name="project_role",
    create_type=False,
    values_callable=lambda enum: [member.value for member in enum],
)


class ProjectUser(Base):
    """Per-project ACL row: grants ``project_role`` to ``user`` on ``project_id``.

    ``project_id`` is TEXT FK to ``projects.id`` (W10-A). Composite PK
    ``(project_id, user_id)`` keeps grants idempotent.
    """

    __tablename__ = "project_users"
    __table_args__ = (
        PrimaryKeyConstraint("project_id", "user_id", name="project_users_pkey"),
        Index("project_users_user_idx", "user_id"),
    )

    project_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_role: Mapped[ProjectRole] = mapped_column(
        _project_role_enum,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    project: Mapped["Project"] = relationship(back_populates="users")


class UsageEvent(Base):
    """Telemetry row written by request-time hooks (login, scan_run, ...).

    ``value_json`` is JSONB so per-event payloads can evolve without
    migrations. Composite indexes serve per-user history and per-kind
    aggregates ordered by recency.
    """

    __tablename__ = "usage_events"
    __table_args__ = (
        Index("usage_events_user_id_ts_idx", "user_id", text("ts DESC")),
        Index("usage_events_kind_ts_idx", "kind", text("ts DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    value_json: Mapped[Any | None] = mapped_column(JSONB)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
