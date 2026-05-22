"""Domain-analysis branch: DomainAnalysis, DomainGroup, DomainAssignment."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, REAL
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flake_analysis.db.base import Base
from flake_analysis.db.models.analysis import PipelineStatus, _pipeline_status_enum

if TYPE_CHECKING:
    from flake_analysis.db.models.analysis import Analysis
    from flake_analysis.db.models.flake_branch import FlakeAnalysis
    from flake_analysis.db.models.sam import Domain


class DomainAnalysis(Base):
    """Selector + clustering committed as a single named entity."""

    __tablename__ = "domain_analyses"
    __table_args__ = (
        UniqueConstraint("analysis_id", "name", name="domain_analyses_analysis_id_name_key"),
        UniqueConstraint("analysis_id", "id", name="domain_analyses_analysis_id_id_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("analyses.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    selector_params: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    selector_params_hash: Mapped[str | None] = mapped_column(Text)
    n_selected_domains: Mapped[int | None] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    clustering_params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    clustering_params_hash: Mapped[str | None] = mapped_column(Text)
    model_s3_uri: Mapped[str | None] = mapped_column(Text)
    status: Mapped[PipelineStatus] = mapped_column(
        _pipeline_status_enum,
        nullable=False,
        server_default=text("'pending'"),
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

    analysis: Mapped[Analysis] = relationship(back_populates="domain_analyses")
    groups: Mapped[list[DomainGroup]] = relationship(
        back_populates="domain_analysis",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    assignments: Mapped[list[DomainAssignment]] = relationship(
        back_populates="domain_analysis",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="DomainAssignment.domain_analysis_id",
    )
    flake_analyses: Mapped[list[FlakeAnalysis]] = relationship(
        back_populates="domain_analysis"
    )


class DomainGroup(Base):
    """Cluster label container (one row per cluster within a DomainAnalysis)."""

    __tablename__ = "domain_groups"
    __table_args__ = (
        UniqueConstraint(
            "domain_analysis_id",
            "cluster_id",
            name="domain_groups_domain_analysis_id_cluster_id_key",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    domain_analysis_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("domain_analyses.id", ondelete="CASCADE"),
        nullable=False,
    )
    cluster_id: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    color: Mapped[str | None] = mapped_column(Text)

    domain_analysis: Mapped[DomainAnalysis] = relationship(back_populates="groups")
    assignments: Mapped[list[DomainAssignment]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DomainAssignment(Base):
    """Domain -> DomainGroup mapping with composite-FK integrity."""

    __tablename__ = "domain_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["analysis_id", "domain_id"],
            ["domains.analysis_id", "domains.id"],
            ondelete="CASCADE",
            name="domain_assignments_analysis_id_domain_id_fkey",
        ),
        ForeignKeyConstraint(
            ["analysis_id", "domain_analysis_id"],
            ["domain_analyses.analysis_id", "domain_analyses.id"],
            ondelete="CASCADE",
            name="domain_assignments_analysis_id_domain_analysis_id_fkey",
        ),
        Index("domain_assignments_group_idx", "domain_group_id"),
    )

    analysis_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    domain_analysis_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        nullable=False,
    )
    domain_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        nullable=False,
    )
    domain_group_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("domain_groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    posterior: Mapped[float | None] = mapped_column(REAL)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    domain_analysis: Mapped[DomainAnalysis] = relationship(
        back_populates="assignments",
        foreign_keys="[DomainAssignment.analysis_id, DomainAssignment.domain_analysis_id]",
        overlaps="domain",
    )
    domain: Mapped[Domain] = relationship(
        foreign_keys="[DomainAssignment.analysis_id, DomainAssignment.domain_id]",
        overlaps="domain_analysis",
    )
    group: Mapped[DomainGroup] = relationship(
        back_populates="assignments",
        foreign_keys=[domain_group_id],
    )
