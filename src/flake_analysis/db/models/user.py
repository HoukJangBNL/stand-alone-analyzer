"""User ORM model (v7: UUID PK + cognito-backed identity columns)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Index, Text, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from flake_analysis.db.base import Base
from flake_analysis.db.models.auth import UserRole


_user_role_enum = postgresql.ENUM(
    UserRole,
    name="user_role",
    create_type=False,
    values_callable=lambda enum: [member.value for member in enum],
)


class User(Base):
    """Application user; FK target for all ``*_by`` columns.

    v7 widened ``id`` from BIGSERIAL to UUID and added cognito-backed
    identity columns. ``username`` is no longer UNIQUE at the column level
    — a partial unique index is created in the migration so multiple rows
    with NULL ``username`` (Cognito-only users) can coexist.
    """

    __tablename__ = "users"
    __table_args__ = (
        Index(
            "users_username_uniq",
            "username",
            unique=True,
            postgresql_where=text("username IS NOT NULL"),
        ),
        Index(
            "users_email_uniq",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    username: Mapped[str | None] = mapped_column(Text)
    cognito_sub: Mapped[str | None] = mapped_column(Text, unique=True)
    email: Mapped[str | None] = mapped_column(Text)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    organization: Mapped[str | None] = mapped_column(Text)
    role: Mapped[UserRole] = mapped_column(
        _user_role_enum,
        nullable=False,
        server_default=text("'member'"),
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
