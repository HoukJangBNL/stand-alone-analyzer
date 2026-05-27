"""Analysis + Run ORM models (pipeline_status enum and step CHECK)."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB, REAL
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
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
    # GENERATED ALWAYS AS (...) STORED — read-only in ORM.
    # The Computed(...) expression below MUST mirror the SQL in
    # alembic/versions/0001_initial_v6.py (CREATE TABLE analyses, status column).
    # Keep both in lock-step; if you change one, change the other in the same PR.
    # Computed is a subclass of FetchedValue and registers itself as both the
    # server_default and server_onupdate generator, so the ORM RETURNING/refresh
    # path automatically pulls the value PostgreSQL just computed. We therefore
    # do NOT pass server_default=FetchedValue() / server_onupdate=FetchedValue()
    # explicitly — SQLAlchemy raises ArgumentError if a Computed column does.
    # nullable=True to match v6 DDL (Postgres can't infer NOT NULL from a
     # GENERATED ALWAYS expression even though our CASE always returns a value).
    status: Mapped[PipelineStatus] = mapped_column(
        _pipeline_status_enum,
        Computed(
            """
            CASE
                WHEN steps_done ? 'failed'
                    THEN 'failed'::pipeline_status
                WHEN steps_done ? 'domain_proximity'
                     AND (steps_done ->> 'domain_proximity')::boolean
                    THEN 'completed'::pipeline_status
                WHEN jsonb_typeof(steps_done) = 'object'
                     AND steps_done <> '{}'::jsonb
                    THEN 'running'::pipeline_status
                ELSE 'pending'::pipeline_status
            END
            """,
            persisted=True,
        ),
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
    created_by_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
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
        # NB: CHECK enum is intentionally narrower than state.paths.PIPELINE_STEPS —
        # thumbnails/selector/clustering/explorer are CPU-only steps that don't
        # write `runs` rows yet. To wire any of those, extend this CHECK in a
        # new alembic migration first.
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
