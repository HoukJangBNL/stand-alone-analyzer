"""GPU pool status service — lazy probe + 30s memory cache.

Backs `GET /api/v1/gpu/status`. Reads EC2 describe_instances (live worker
detection) and describe_spot_price_history (capacity heuristic) and
classifies the pool into one of five coarse states. Single-instance
in-memory cache only — when the API scales out we'll move to Redis.

The cache is an asyncio-aware module-level tuple guarded by an
asyncio.Lock to prevent thundering herd: concurrent first-callers all
wait on the same probe instead of each issuing their own AWS round-trip.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from flake_analysis.api.schemas.gpu import GpuPoolStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Launch template name (owned by `scripts/aws/sam-launch-template.sh`).
#: Mirrors `flake_analysis.worker.launcher.LAUNCH_TEMPLATE_NAME`.
SAM_GPU_LAUNCH_TEMPLATE_NAME: str = "qpress-sam-gpu-worker"

#: Region where the GPU pool lives. Same source of truth as the launcher.
AWS_REGION: str = "us-east-2"

#: AZs to probe for spot capacity. us-east-2 has three AZs; we query all.
PROBE_AVAILABILITY_ZONES: tuple[str, ...] = (
    "us-east-2a",
    "us-east-2b",
    "us-east-2c",
)

#: g6e.48xlarge — the only instance type the SAM pipeline runs on.
GPU_INSTANCE_TYPE: str = "g6e.48xlarge"

#: Tag filters for "this is a SAM GPU worker". Aligned with the existing
#: production launcher (`worker/launcher.py:_describe_filters`) so we
#: classify the same fleet of instances.
TAG_PROJECT: str = "qpress-sam"
TAG_ROLE_WORKER: str = "worker"

#: EC2 states that count as "alive" for our purposes. Excludes
#: shutting-down/stopping (those won't pick up new SAM jobs).
_LIVE_INSTANCE_STATES: tuple[str, ...] = ("pending", "running")

#: Cache TTL. Short enough that the badge doesn't get badly stale, long
#: enough that the AWS bill stays small even under heavy badge polling.
CACHE_TTL_SECONDS: int = 30

#: Spot price freshness threshold. Prices older than this are treated as
#: "no recent activity" → unavailable_capacity heuristic.
SPOT_PRICE_FRESH_WINDOW_SECONDS: int = 3600  # 1 hour


# ---------------------------------------------------------------------------
# Module-level cache (single-instance only — see module docstring)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CacheEntry:
    """Cached GpuPoolStatus + the wall-clock time it was stored."""

    status: GpuPoolStatus
    stored_at: datetime


_cache: _CacheEntry | None = None
_cache_lock: asyncio.Lock = asyncio.Lock()


def _now_utc() -> datetime:
    """Indirection for tests to monkeypatch the clock."""
    return datetime.now(timezone.utc)


def _ec2_client_factory() -> Any:
    """Build a regional boto3 EC2 client.

    Tests monkeypatch this to inject a Stubber-wrapped client.
    """
    return boto3.client("ec2", region_name=AWS_REGION)


def reset_cache_for_tests() -> None:
    """Clear the module-level cache. Used by test fixtures."""
    global _cache
    _cache = None


# ---------------------------------------------------------------------------
# State derivation (synchronous — runs in executor)
# ---------------------------------------------------------------------------


def _describe_filters() -> list[dict[str, Any]]:
    """EC2 describe_instances filters for live SAM workers."""
    return [
        {"Name": "tag:Project", "Values": [TAG_PROJECT]},
        {"Name": "tag:Role", "Values": [TAG_ROLE_WORKER]},
        {"Name": "instance-state-name", "Values": list(_LIVE_INSTANCE_STATES)},
    ]


def _classify_instances(resp: dict[str, Any]) -> tuple[str, str] | None:
    """Inspect describe_instances response.

    Returns (state, detail) where state is "running" or "launching", or
    None if no live instance is present.
    """
    running: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for reservation in resp.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            name = (inst.get("State") or {}).get("Name")
            if name == "running":
                running.append(inst)
            elif name == "pending":
                pending.append(inst)

    if running:
        first = running[0]
        az = (first.get("Placement") or {}).get("AvailabilityZone", "?")
        return ("running", f"{GPU_INSTANCE_TYPE} running in {az}")
    if pending:
        first = pending[0]
        az = (first.get("Placement") or {}).get("AvailabilityZone", "?")
        return ("launching", f"{GPU_INSTANCE_TYPE} launching in {az}")
    return None


def _latest_price_per_az(
    resp: dict[str, Any],
    *,
    fresh_cutoff: datetime,
) -> dict[str, float]:
    """Pick the most recent SpotPrice per AZ from a describe_spot_price_history.

    Filters out entries older than `fresh_cutoff`.
    """
    latest: dict[str, tuple[datetime, float]] = {}
    for entry in resp.get("SpotPriceHistory", []):
        az = entry.get("AvailabilityZone")
        ts = entry.get("Timestamp")
        price_str = entry.get("SpotPrice")
        if not az or not ts or price_str is None:
            continue
        # Boto returns timezone-aware datetimes; defensive coerce just in case.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < fresh_cutoff:
            continue
        try:
            price = float(price_str)
        except (TypeError, ValueError):
            continue
        existing = latest.get(az)
        if existing is None or ts > existing[0]:
            latest[az] = (ts, price)
    return {az: price for az, (_ts, price) in latest.items()}


def _probe_sync(ec2: Any, *, now: datetime) -> GpuPoolStatus:
    """Single AWS probe round (blocking). Called inside run_in_executor.

    Implements the state precedence: running > launching > ready >
    unavailable_capacity > unknown.
    """
    # ------------------------------------------------------------------ 1/2
    # describe_instances first — running/launching short-circuit the rest.
    try:
        di_resp = ec2.describe_instances(Filters=_describe_filters())
    except (ClientError, BotoCoreError) as e:
        return _unknown_status(e, now=now)

    classified = _classify_instances(di_resp)
    if classified is not None:
        state, detail = classified
        return GpuPoolStatus(
            state=state,  # type: ignore[arg-type]
            detail=detail,
            checked_at=now,
            spot_prices_usd_per_hr=None,
        )

    # ------------------------------------------------------------------ 2/2
    # No live instance — check spot price history for capacity signal.
    fresh_cutoff = now - timedelta(seconds=SPOT_PRICE_FRESH_WINDOW_SECONDS)
    # NOTE: describe_spot_price_history takes `AvailabilityZone` (singular)
    # not a list. We omit the AZ filter and let the response include all
    # AZs in the region, then post-filter to PROBE_AVAILABILITY_ZONES below.
    try:
        sp_resp = ec2.describe_spot_price_history(
            InstanceTypes=[GPU_INSTANCE_TYPE],
            StartTime=fresh_cutoff,
            ProductDescriptions=["Linux/UNIX"],
            MaxResults=len(PROBE_AVAILABILITY_ZONES) * 5,
        )
    except (ClientError, BotoCoreError) as e:
        return _unknown_status(e, now=now)

    prices = _latest_price_per_az(sp_resp, fresh_cutoff=fresh_cutoff)
    # Restrict to the AZs we care about (us-east-2 may add new AZs we
    # haven't certified yet, and we don't want their prices leaking in).
    prices = {az: p for az, p in prices.items() if az in PROBE_AVAILABILITY_ZONES}
    if prices:
        return GpuPoolStatus(
            state="ready",
            detail=f"spot pool active across {len(prices)} AZ(s)",
            checked_at=now,
            spot_prices_usd_per_hr=prices,
        )

    # No fresh spot prices, but the launcher guarantees on-demand fallback
    # across a 4-tier instance-type ladder (INSTANCE_TYPE_LADDER in
    # worker/launcher.py) with multi-AZ retries. On-demand is almost always
    # available outside of multi-region AWS outages. Return "ready" to
    # reflect real launchability — the dispatcher will fall back to
    # on-demand on the next run.
    #
    # "unavailable_capacity" is reserved for situations where we have
    # evidence that NOTHING can launch (e.g., dry-run RunInstances across
    # the ladder returns InsufficientInstanceCapacity for all tiers in all
    # markets). Since that's too expensive/slow for a status probe, we
    # don't claim unavailability here.
    return GpuPoolStatus(
        state="ready",
        detail="no live worker — will launch on-demand on next run",
        checked_at=now,
        spot_prices_usd_per_hr=None,
    )


def _unknown_status(exc: Exception, *, now: datetime) -> GpuPoolStatus:
    """Build an 'unknown' status from a botocore exception.

    Truncates the exception message to keep the JSON envelope small —
    the frontend only renders this in a tooltip.
    """
    code = ""
    if isinstance(exc, ClientError):
        code = (exc.response.get("Error") or {}).get("Code", "") or ""
    msg = str(exc)
    if len(msg) > 240:
        msg = msg[:240] + "..."
    label = code or type(exc).__name__
    return GpuPoolStatus(
        state="unknown",
        detail=f"{label}: {msg}" if msg else label,
        checked_at=now,
        spot_prices_usd_per_hr=None,
    )


# ---------------------------------------------------------------------------
# Public entry-point (async)
# ---------------------------------------------------------------------------


async def get_gpu_pool_status() -> GpuPoolStatus:
    """Return cached status if fresh, else probe AWS and cache the result.

    Thundering-herd protection: concurrent callers all serialize on
    `_cache_lock`. The first one inside the critical section may probe;
    later callers see the just-stored cache entry and reuse it.
    """
    global _cache

    now = _now_utc()
    cached = _cache
    if cached is not None and (now - cached.stored_at).total_seconds() < CACHE_TTL_SECONDS:
        return cached.status

    async with _cache_lock:
        # Re-check under the lock — a concurrent caller may have just refreshed.
        now = _now_utc()
        cached = _cache
        if cached is not None and (now - cached.stored_at).total_seconds() < CACHE_TTL_SECONDS:
            return cached.status

        ec2 = _ec2_client_factory()
        loop = asyncio.get_running_loop()
        status = await loop.run_in_executor(None, lambda: _probe_sync(ec2, now=now))

        _cache = _CacheEntry(status=status, stored_at=now)
        return status
