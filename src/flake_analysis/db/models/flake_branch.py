"""Flake-analysis branch: FlakeAnalysis (explorer session) and FlakeCuration."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flake_analysis.db.base import Base

if TYPE_CHECKING:
    from flake_analysis.db.models.analysis import Analysis
    from flake_analysis.db.models.domain_branch import DomainAnalysis
    from flake_analysis.db.models.sam import Flake


class FlakeAnalysis(Base):
    """Explorer session over an analysis, optionally cross-linked to a DomainAnalysis."""

    __tablename__ = "flake_analyses"
    __table_args__ = (
        UniqueConstraint("analysis_id", "name", name="flake_analyses_analysis_id_name_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("analyses.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain_analysis_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("domain_analyses.id", ondelete="SET NULL"),
    )
    explorer_params: Mapped[dict | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(Text)
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
    created_by_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id"),
    )

    analysis: Mapped[Analysis] = relationship(back_populates="flake_analyses")
    domain_analysis: Mapped[DomainAnalysis | None] = relationship(
        back_populates="flake_analyses"
    )
    curations: Mapped[list[FlakeCuration]] = relationship(
        back_populates="flake_analysis",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class FlakeCuration(Base):
    """User-curated tag/notes attached to a Flake within a FlakeAnalysis."""

    __tablename__ = "flake_curations"
    __table_args__ = (
        UniqueConstraint(
            "flake_analysis_id",
            "flake_id",
            name="flake_curations_flake_analysis_id_flake_id_key",
        ),
        Index("flake_curations_flake_idx", "flake_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    flake_analysis_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("flake_analyses.id", ondelete="CASCADE"),
        nullable=False,
    )
    flake_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("flakes.id", ondelete="CASCADE"),
        nullable=False,
    )
    tag: Mapped[str | None] = mapped_column(Text)
    is_of_interest: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("FALSE"),
    )
    notes: Mapped[str | None] = mapped_column(Text)
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
    created_by_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id"),
    )

    flake_analysis: Mapped[FlakeAnalysis] = relationship(back_populates="curations")
    flake: Mapped[Flake] = relationship()
