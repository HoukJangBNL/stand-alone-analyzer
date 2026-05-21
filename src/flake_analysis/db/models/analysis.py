"""Analysis + Run ORM models (pipeline_status enum and step CHECK)."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Literal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    FetchedValue,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB, REAL
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flake_analysis.db.base import Base

if TYPE_CHECKING:
    from flake_analysis.db.models.catalog import Model, Scan
    from flake_analysis.db.models.domain_branch import DomainAnalysis
    from flake_analysis.db.models.flake_branch import FlakeAnalysis
    from flake_analysis.db.models.sam import Domain, Flake


PipelineStep = Literal["background", "sam", "domain_stats", "domain_proximity"]


class PipelineStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


_pipeline_status_enum = postgresql.ENUM(
    PipelineStatus,
    name="pipeline_status",
    create_type=False,
    values_callable=lambda enum: [member.value for member in enum],
)


class Analysis(Base):
    """(scan, model, params) unit. status is a GENERATED column - read-only in ORM."""

    __tablename__ = "analyses"
    __table_args__ = (
        Index(
            "analyses_scan_model_name_uniq",
            "scan_id",
            "model_id",
            "name",
            unique=True,
            postgresql_where=text("name IS NOT NULL"),
        ),
        Index("analyses_scan_idx", "scan_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    scan_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("scans.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("models.id"),
        nullable=False,
    )
    name: Mapped[str | None] = mapped_column(Text)
    amg_params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    background_params: Mapped[dict | None] = mapped_column(JSONB)
    background_s3_uri: Mapped[str | None] = mapped_column(Text)
    link_distance_px: Mapped[float] = mapped_column(REAL, nullable=False)
    min_area_px: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("10"),
    )
    max_area_px: Mapped[int | None] = mapped_column(Integer)
    proximity_params: Mapped[dict | None] = mapped_column(JSONB)
    steps_done: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    # GENERATED ALWAYS AS (...) STORED — never written by ORM, refresh after I/O.
    status: Mapped[PipelineStatus] = mapped_column(
        _pipeline_status_enum,
        server_default=FetchedValue(),
        server_onupdate=FetchedValue(),
        nullable=False,
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

    scan: Mapped[Scan] = relationship(back_populates="analyses")
    model: Mapped[Model] = relationship(back_populates="analyses")
    runs: Mapped[list[Run]] = relationship(
        back_populates="analysis",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    domains: Mapped[list[Domain]] = relationship(
        back_populates="analysis",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    flakes: Mapped[list[Flake]] = relationship(
        back_populates="analysis",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    domain_analyses: Mapped[list[DomainAnalysis]] = relationship(
        back_populates="analysis",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    flake_analyses: Mapped[list[FlakeAnalysis]] = relationship(
        back_populates="analysis",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Run(Base):
    """Per-step execution attempt (audit log)."""

    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(
            "step IN ('background', 'sam', 'domain_stats', 'domain_proximity')",
            name="runs_step_check",
        ),
        Index("runs_analysis_idx", "analysis_id"),
        Index("runs_analysis_step_idx", "analysis_id", "step"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("analyses.id", ondelete="CASCADE"),
        nullable=False,
    )
    step: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[PipelineStatus] = mapped_column(
        _pipeline_status_enum,
        nullable=False,
    )
    instance_type: Mapped[str | None] = mapped_column(Text)
    instance_id: Mapped[str | None] = mapped_column(Text)
    is_spot: Mapped[bool | None] = mapped_column(Boolean)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    analysis: Mapped[Analysis] = relationship(back_populates="runs")
