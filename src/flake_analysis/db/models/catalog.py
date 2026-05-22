"""Catalog-level ORM models: Model (LoRA checkpoint) and Scan (upload batch)."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flake_analysis.db.base import Base

if TYPE_CHECKING:
    from flake_analysis.db.models.analysis import Analysis
    from flake_analysis.db.models.upload import Image, UploadSession


class Model(Base):
    """LoRA checkpoint metadata."""

    __tablename__ = "models"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    base_model: Mapped[str] = mapped_column(Text, nullable=False)
    s3_uri: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    analyses: Mapped[list[Analysis]] = relationship(back_populates="model")


class Scan(Base):
    """User upload batch / experiment unit."""

    __tablename__ = "scans"
    __table_args__ = (
        Index(
            "scans_material_idx",
            "material",
            postgresql_where=text("material IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    material: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    image_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    created_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
    )

    images: Mapped[list[Image]] = relationship(
        back_populates="scan",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    analyses: Mapped[list[Analysis]] = relationship(
        back_populates="scan",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    upload_sessions: Mapped[list[UploadSession]] = relationship(
        back_populates="scan",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Material(Base):
    """Controlled vocabulary for scan material types.

    `name` is the natural primary key; W5 uploads validate `scans.material`
    as a foreign key to `materials.name`. New names are auto-added via
    `INSERT ... ON CONFLICT DO NOTHING` on first user input.
    """

    __tablename__ = "materials"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    created_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
