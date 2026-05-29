"""WorkerEvent ORM model — sink for run_sam timing markers and lifecycle events.

Distinct from usage_events (which is per-user telemetry):
* No user_id FK — workers run without an authenticated user context.
* Indexed by (run_id, ts) for measurement-time analytics.
* Append-only; no updates, no deletes.

Writers: src/flake_analysis/worker/markers.py::emit_marker (sync psycopg)
         src/flake_analysis/worker/tasks.py (via emit_marker)

Schema lives in alembic 0007_worker_events.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from flake_analysis.db.base import Base


class WorkerEvent(Base):
    """Append-only row for a single timing marker or worker lifecycle event."""

    __tablename__ = "worker_events"
    __table_args__ = (
        Index("worker_events_run_id_ts_idx", "run_id", text("ts DESC")),
        Index("worker_events_event_ts_idx", "event", text("ts DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[Any | None] = mapped_column(JSONB)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
