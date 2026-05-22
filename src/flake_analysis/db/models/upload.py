"""Upload + Image ORM models: UploadSession, UploadItem, Image."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CHAR,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import REAL
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flake_analysis.db.base import Base

if TYPE_CHECKING:
    from flake_analysis.db.models.catalog import Scan
    from flake_analysis.db.models.sam import Domain


class UploadSessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABORTED = "aborted"


class UploadItemStatus(str, Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    FAILED = "failed"


class UploadSession(Base):
    """Batch upload tracking with progress counters."""

    __tablename__ = "upload_sessions"
    __table_args__ = (
        Index("upload_sessions_scan_idx", "scan_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("scans.id", ondelete="CASCADE"),
        nullable=False,
    )
    total_files: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_files: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    failed_files: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    status: Mapped[UploadSessionStatus] = mapped_column(
        postgresql.ENUM(
            UploadSessionStatus,
            name="upload_session_status",
            create_type=False,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        server_default=text("'active'"),
    )
    manifest_s3_uri: Mapped[str | None] = mapped_column(Text)
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

    scan: Mapped[Scan] = relationship(back_populates="upload_sessions")
    items: Mapped[list[UploadItem]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Image(Base):
    """Successfully-uploaded image (canonical metadata target)."""

    __tablename__ = "images"
    __table_args__ = (
        UniqueConstraint("scan_id", "sha256", name="images_scan_id_sha256_key"),
        Index("images_scan_idx", "scan_id"),
        Index(
            "images_grid_idx",
            "scan_id",
            "grid_ix",
            "grid_iy",
            postgresql_where=text("grid_ix IS NOT NULL AND grid_iy IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("scans.id", ondelete="CASCADE"),
        nullable=False,
    )
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    s3_uri: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str | None] = mapped_column(Text)
    grid_ix: Mapped[int | None] = mapped_column(Integer)
    grid_iy: Mapped[int | None] = mapped_column(Integer)
    stage_x_um: Mapped[float | None] = mapped_column(REAL)
    stage_y_um: Mapped[float | None] = mapped_column(REAL)
    pixel_size_um: Mapped[float | None] = mapped_column(REAL)
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

    scan: Mapped[Scan] = relationship(back_populates="images")
    domains: Mapped[list[Domain]] = relationship(back_populates="image")


class UploadItem(Base):
    """Per-file upload state with retry info and manifest metadata."""

    __tablename__ = "upload_items"
    __table_args__ = (
        UniqueConstraint("session_id", "sha256", name="upload_items_session_id_sha256_key"),
        Index(
            "upload_items_session_status_idx",
            "session_id",
            "status",
            postgresql_where=text("status IN ('pending', 'uploading')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("upload_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[UploadItemStatus] = mapped_column(
        postgresql.ENUM(
            UploadItemStatus,
            name="upload_item_status",
            create_type=False,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        server_default=text("'pending'"),
    )
    s3_uri: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    image_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("images.id"),
    )
    grid_ix: Mapped[int | None] = mapped_column(Integer)
    grid_iy: Mapped[int | None] = mapped_column(Integer)
    stage_x_um: Mapped[float | None] = mapped_column(REAL)
    stage_y_um: Mapped[float | None] = mapped_column(REAL)
    pixel_size_um: Mapped[float | None] = mapped_column(REAL)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped[UploadSession] = relationship(back_populates="items")
    image: Mapped[Image | None] = relationship()
