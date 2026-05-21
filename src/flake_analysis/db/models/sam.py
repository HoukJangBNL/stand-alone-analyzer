"""SAM-output ORM models: Flake (analysis-scoped) and Domain (per-mask)."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, REAL
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flake_analysis.db.base import Base

if TYPE_CHECKING:
    from flake_analysis.db.models.analysis import Analysis
    from flake_analysis.db.models.upload import Image


class Flake(Base):
    """Analysis-scoped flake (cross-image-ready)."""

    __tablename__ = "flakes"
    __table_args__ = (
        CheckConstraint(
            "coordinate_system IN ('image_px', 'stage_um')",
            name="flakes_coordinate_system_check",
        ),
        Index("flakes_analysis_idx", "analysis_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("analyses.id", ondelete="CASCADE"),
        nullable=False,
    )
    coordinate_system: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'image_px'"),
    )
    anchor_image_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("images.id"),
    )
    n_domains: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    area: Mapped[int] = mapped_column(Integer, nullable=False)
    segmentation_rle: Mapped[dict | list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    analysis: Mapped[Analysis] = relationship(back_populates="flakes")
    anchor_image: Mapped[Image | None] = relationship()
    domains: Mapped[list[Domain]] = relationship(back_populates="flake")


class Domain(Base):
    """SAM mask row; carries composite UNIQUE(analysis_id, id) for cross-FKs."""

    __tablename__ = "domains"
    __table_args__ = (
        UniqueConstraint("analysis_id", "id", name="domains_analysis_id_id_key"),
        Index("domains_analysis_image_idx", "analysis_id", "image_id"),
        Index("domains_image_idx", "image_id"),
        Index(
            "domains_flake_idx",
            "flake_id",
            postgresql_where=text("flake_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("analyses.id", ondelete="CASCADE"),
        nullable=False,
    )
    image_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("images.id"),
        nullable=False,
    )
    flake_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("flakes.id", ondelete="SET NULL"),
    )
    bbox: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    area: Mapped[int] = mapped_column(Integer, nullable=False)
    segmentation_rle: Mapped[dict | list] = mapped_column(JSONB, nullable=False)
    sam_score: Mapped[float | None] = mapped_column(REAL)
    stats: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    analysis: Mapped[Analysis] = relationship(back_populates="domains")
    image: Mapped[Image] = relationship(back_populates="domains")
    flake: Mapped[Flake | None] = relationship(back_populates="domains")
