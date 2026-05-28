"""GPU pool status schema (read by ComputeTab badge).

The GpuPoolStatus model is the JSON contract for `GET /api/v1/gpu/status`.
Five-state enum is intentionally narrow: the frontend only needs to render
a small badge, not full EC2 telemetry.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class GpuPoolStatus(BaseModel):
    """Lazy-probed snapshot of the SAM GPU worker pool.

    Fields:
        state: Coarse availability classification. Order of precedence on
            the server (first match wins): running > launching > ready >
            unavailable_capacity > unknown.
        detail: Human-readable explanation. Surfaced verbatim in the
            ComputeTab tooltip — keep it short and actionable.
        checked_at: UTC timestamp of the AWS probe (NOT request time).
            Two responses with identical checked_at were served from the
            same cache entry.
        spot_prices_usd_per_hr: Most recent g6e.48xlarge spot price per
            AZ when known. None when probe failed or the spot pool is
            empty. Keyed by AZ name (e.g. "us-east-2a").
    """

    state: Literal[
        "ready",
        "launching",
        "unavailable_capacity",
        "running",
        "unknown",
    ]
    detail: str
    checked_at: datetime
    spot_prices_usd_per_hr: dict[str, float] | None
