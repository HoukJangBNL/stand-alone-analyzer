"""API-side GPU worker launcher (P4.4).

Boots a GPU worker EC2 instance on demand when no live worker is
draining the procrastinate ``gpu`` queue. Called from the SAM and
pipeline routes immediately before ``defer_async``.

Why this lives here (not in the SAM route)
-------------------------------------------
- Fan-in: both ``POST /run/sam`` and ``POST /run/pipeline`` defer SAM
  jobs. Centralising the "is there a worker?" check avoids divergence.
- Test seam: tests inject a fake EC2 client + advisory lock without
  going anywhere near boto3 or PG.

Race protection
---------------
Two concurrent SAM defers from independent requests would both see "no
worker running" and each call ``RunInstances``, booting two spot boxes
for one job. We serialise the boot-window with a transient PG advisory
lock keyed on a constant integer (``ADVISORY_LOCK_KEY``). Whichever
caller wins the lock does the describe → run; the other sees the lock
held and is a no-op.

The advisory lock object is injected (the production caller wires it
to a session-scoped ``pg_try_advisory_lock``); tests pass a fake
:class:`AdvisoryLock`-shaped object to assert acquire/release semantics.

Cold start
----------
The boot itself takes 3-5 minutes (CUDA + repo clone + uv sync +
weights download). The API does NOT wait for the worker to come
online — it just kicks off the boot and returns. The SSE stream stays
open; from the frontend's perspective, the SAM step shows "running"
with progress=0 until the worker comes up, claims the procrastinate
job, and starts emitting NOTIFY frames.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import boto3
import psycopg
from botocore.exceptions import ClientError

from flake_analysis.db.url import DbSettings, _require_ssl

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Name of the EC2 launch template that boots a GPU worker. Owned by
#: ``scripts/aws/sam-launch-template.sh``.
LAUNCH_TEMPLATE_NAME: str = "qpress-sam-gpu-worker"

#: Region where the launch template + IAM + SG live.
AWS_REGION: str = "us-east-2"

#: Multi-AZ subnet rotation list (T7n, owner directive 2026-06-11).
#: The LT pins a single subnet (us-east-2a) into its NetworkInterfaces
#: block, but g6e.48xlarge capacity flits between AZs in real time —
#: 2026-06-11 §40 saw all three AZs rotate "drought" / "available" within
#: seconds of each other. _launch_one tries each AZ in turn (spot first,
#: then on-demand), giving us 6 boot attempts before raising
#: GpuCapacityUnavailable. SubnetId override on RunInstances supersedes
#: the LT's NetworkInterfaces.SubnetId, and the SG also lives at the VPC
#: level (sg-0e57146d5b6d42452 covers all three AZs).
SUBNETS_BY_AZ: dict[str, str] = {
    "us-east-2a": "subnet-0fe8558512beea68a",
    "us-east-2b": "subnet-0fe98fb0f6d63afc3",
    "us-east-2c": "subnet-09f76839fd0c109a9",
}

#: Security group attached when overriding the LT's NetworkInterfaces.
#: Created by ``scripts/aws/sam-iam-bootstrap.sh``; same group across AZs.
WORKER_SECURITY_GROUP_ID: str = "sg-0e57146d5b6d42452"

#: GPU-count ladder of instance types — try the largest first
#: (most throughput), fall back to smaller ones if capacity fails.
#: Owner directive 2026-06-11: "8장부터 시작해서 실패하면 7장 이런식으로
#: 내려와서 최대한 빨리 실행하게" — g6e family only ships 8/4/4/1 GPU
#: SKUs, so the ladder picks one of each tier. Each tier gets the
#: full spot-3AZ + on-demand-3AZ probe (T7n) before stepping down.
#:
#: g6e.48xlarge: 8 GPUs ($5.96 spot / $7.23 on-demand)
#: g6e.24xlarge: 4 GPUs ($2.97 spot / ~$3.61 on-demand)
#: g6e.12xlarge: 4 GPUs ($1.86 spot / ~$2.52 on-demand) — best price/perf
#: g6e.4xlarge:  1 GPU  ($0.62 spot / ~$0.77 on-demand) — almost always
#:               available; floor of the ladder.
INSTANCE_TYPE_LADDER: tuple[str, ...] = (
    "g6e.48xlarge",
    "g6e.24xlarge",
    "g6e.12xlarge",
    "g6e.4xlarge",
)

#: Tag values used to identify production GPU workers (cost-allocation
#: tag is also ``Project=qpress-sam`` for budget filtering — see P4.5).
TAG_PROJECT: str = "qpress-sam"
TAG_ROLE_WORKER: str = "worker"

#: PG advisory-lock key for the boot window. Shared by all defer
#: callers. Value is arbitrary but stable ('0xCAFE0044' = "P4.4 boot
#: window"). The production caller passes this to
#: ``pg_try_advisory_lock(key)``.
ADVISORY_LOCK_KEY: int = 0xCAFE0044


def _market_preference() -> str:
    """Read SAM_GPU_MARKET env var to determine market order.

    Returns:
        "ondemand" (default) — skip spot, try on-demand only
        "spot-first" — legacy behavior (spot → on-demand fallback per tier)

    Context (2026-06-16): spot instances get reclaimed mid-run by AWS
    (Server.SpotInstanceTermination / instance-terminated-no-capacity),
    wasting compute. Owner directive: always use on-demand so runs
    complete. Cost delta: g6e.48xlarge $5.96 spot → $7.23 on-demand
    (~21% higher), acceptable trade for reliability.
    """
    return os.environ.get("SAM_GPU_MARKET", "ondemand")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class GpuCapacityUnavailable(RuntimeError):
    """Spot capacity exhausted — surface as pipeline_error to the user.

    Wraps boto3's ``InsufficientInstanceCapacity`` ClientError so the
    API layer doesn't need to know boto3 error shapes. The toast
    message ("GPU capacity unavailable, retry later") comes from str(e).
    """


# ---------------------------------------------------------------------------
# Injection points
# ---------------------------------------------------------------------------


class AdvisoryLock(Protocol):
    """Async context-manager-ish lock contract.

    The production implementation wraps a PG ``pg_try_advisory_lock``
    call on a dedicated, short-lived connection. Returning ``False``
    from ``acquire()`` means another caller already holds the lock —
    this caller should treat the boot as someone else's problem and
    no-op.
    """

    async def acquire(self) -> bool: ...
    async def release(self) -> None: ...


Ec2ClientFactory = Callable[[], Any]
"""Zero-arg callable returning a boto3 EC2 client (or a test fake).

Default factory builds a regional boto3 client. Tests pass a lambda
that returns a :class:`tests.worker.test_launcher.FakeEc2Client`.
"""


def _default_ec2_client_factory() -> Any:
    """Build a real regional boto3 EC2 client. Uses the host's default
    credential chain (instance profile in prod, env/profile locally).
    """
    return boto3.client("ec2", region_name=AWS_REGION)


# ---------------------------------------------------------------------------
# Production advisory lock (PG-backed)
# ---------------------------------------------------------------------------


class PgAdvisoryLock:
    """Production advisory lock backed by ``pg_try_advisory_lock``.

    Opens a dedicated short-lived psycopg connection (the lock is
    session-scoped — releasing the connection releases the lock, even
    if the API process crashes mid-launch). The connection stays open
    only between :py:meth:`acquire` and :py:meth:`release`.

    Tests use the :class:`AdvisoryLock` Protocol with a fake instead of
    this class, so this code path is not unit-tested in
    ``test_launcher.py``. It's covered by the integration smoke step
    documented in ``docs/sam-ops.md``.
    """

    def __init__(self, key: int = ADVISORY_LOCK_KEY) -> None:
        self._key = key
        self._conn: psycopg.Connection | None = None

    @staticmethod
    def _conn_kwargs() -> dict[str, Any]:
        s = DbSettings()
        kwargs: dict[str, Any] = {
            "host": s.db_host,
            "port": s.db_port,
            "dbname": s.db_name,
        }
        if _require_ssl(s.db_host):
            # RDS rds.force_ssl=1: SSL-only, no prefer→fallback. See #217.
            kwargs["sslmode"] = "require"
        if s.db_user:
            kwargs["user"] = s.db_user
        if s.db_password:
            kwargs["password"] = s.db_password
        return kwargs

    async def acquire(self) -> bool:
        loop = asyncio.get_running_loop()

        def _sync_acquire() -> bool:
            conn = psycopg.connect(**self._conn_kwargs(), autocommit=True)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_try_advisory_lock(%s)", (self._key,))
                    row = cur.fetchone()
                    got = bool(row and row[0])
                if got:
                    self._conn = conn
                    return True
                # Didn't get the lock — close the connection immediately.
                conn.close()
                return False
            except Exception:
                conn.close()
                raise

        return await loop.run_in_executor(None, _sync_acquire)

    async def release(self) -> None:
        if self._conn is None:
            return
        loop = asyncio.get_running_loop()
        conn = self._conn
        self._conn = None

        def _sync_release() -> None:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (self._key,))
            finally:
                conn.close()

        await loop.run_in_executor(None, _sync_release)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LaunchResult:
    """Outcome of an :func:`ensure_worker_running` call.

    ``action``:
      - ``"launched"``  — we called RunInstances and got an instance id back.
      - ``"noop"``      — no boot needed; ``reason`` carries the why.
    """

    action: str  # "launched" | "noop"
    reason: str | None = None  # populated when action == "noop"
    instance_id: str | None = None  # populated when action == "launched"


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


_LIVE_INSTANCE_STATES: tuple[str, ...] = ("pending", "running")
"""EC2 states that count as "a worker exists for this queue".

Excludes ``shutting-down`` and ``stopping`` — those instances are on
their way out and won't pick up new procrastinate jobs reliably. If
they're the only thing running, we boot a fresh one.
"""


def _describe_filters() -> list[dict[str, Any]]:
    """Build the EC2 describe-instances filters used to find live workers.

    Filters on:
      - ``tag:Project = qpress-sam``  (cost-allocation tag, also distinguishes
        from any other random EC2 in the account)
      - ``tag:Role    = worker``       (excludes the bootstrap-mode instance
        that produces merged.pt during P4.3 Phase 2)
      - ``instance-state-name in (pending, running)``
    """
    return [
        {"Name": "tag:Project", "Values": [TAG_PROJECT]},
        {"Name": "tag:Role", "Values": [TAG_ROLE_WORKER]},
        {"Name": "instance-state-name", "Values": list(_LIVE_INSTANCE_STATES)},
    ]


def _has_live_worker(ec2: Any) -> bool:
    """Synchronous boto3 call: is there a worker in pending/running?"""
    resp = ec2.describe_instances(Filters=_describe_filters())
    for reservation in resp.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            state = (inst.get("State") or {}).get("Name")
            if state in _LIVE_INSTANCE_STATES:
                return True
    return False


# Spot-only failure modes that mean "no spot for you right now" but
# don't indicate a fundamental problem with the request. All of these
# warrant an immediate on-demand retry — owner directive 2026-06-08:
# "spot fail 하면 자동으로 ondemand 로 넘어가게 해".
#
# - InsufficientInstanceCapacity: AWS has no spot capacity right now.
# - MaxSpotInstanceCountExceeded: our spot quota is full (often a
#   recent terminate hasn't released the spot request yet — it stays
#   `active/fulfilled` for ~10 min after instance termination).
# - SpotMaxPriceTooLow: our bid is below the current spot price.
# - SpotInstanceCountLimitExceeded: alternate name AWS sometimes returns.
# - Unsupported: returned for spot when an instance type isn't
#   available as spot in a given subnet/AZ at this moment.
_SPOT_FAILURE_CODES = frozenset({
    "InsufficientInstanceCapacity",
    "MaxSpotInstanceCountExceeded",
    "SpotMaxPriceTooLow",
    "SpotInstanceCountLimitExceeded",
    "Unsupported",
})

# On-demand transient capacity/quota errors that should continue the
# ladder (step down to smaller instance type) rather than abort. These
# are distinct from genuine bugs (IAM, malformed template) and hard
# account quotas which must propagate immediately.
#
# Bug context (2026-06-12): observed MaxSpotInstanceCountExceeded raised
# during on-demand RunInstances when recent spot requests hadn't cleared.
# AWS surfaces account-level spot quota state even on on-demand calls,
# and the spot-only error codes can leak. Without this tolerance, the
# launcher kills the ladder on the first on-demand call instead of
# stepping down to smaller instance types that might succeed.
#
# - InsufficientInstanceCapacity: genuine capacity shortage. Stepping
#   down to a smaller tier (fewer GPUs) or trying next AZ may succeed.
# - MaxSpotInstanceCountExceeded: account spot-limit residue leaking
#   onto on-demand calls; transient, continue ladder.
# - SpotInstanceCountLimitExceeded: alternate name for spot quota leak.
#
# NOT included (must propagate immediately):
# - UnauthorizedOperation: IAM misconfiguration; ladder can't fix this.
# - InvalidParameterValue: bug in our template/override logic; not transient.
# - InvalidParameterCombination: also a code bug, not a capacity issue.
# - VcpuLimitExceeded: hard account vCPU quota ceiling. If the account
#   is at its limit, ALL tiers will fail with the same error. Stepping
#   down burns 24 attempts then raises GpuCapacityUnavailable, masking
#   an actionable "request quota increase" signal. Propagate immediately
#   so the operator sees the real error.
# - InstanceLimitExceeded: hard account instance-count quota. Same
#   rationale as VcpuLimitExceeded — propagate for operator diagnosis.
#
# General rule: only transient capacity / spot-quota-leak errors belong
# here. Hard account quotas (Vcpu/InstanceLimitExceeded) and
# configuration bugs (IAM, subnet, AMI, parameter validation) must
# surface immediately for operator diagnosis.
_ONDEMAND_FALLBACK_CODES = frozenset({
    "InsufficientInstanceCapacity",
    "MaxSpotInstanceCountExceeded",
    "SpotInstanceCountLimitExceeded",
})


def _try_run_instances(
    ec2: Any,
    *,
    instance_type: str,
    subnet_id: str,
    on_demand: bool,
) -> str | None:
    """One RunInstances attempt with a specific instance type, subnet,
    and market type.

    Overrides the LT's pinned ``InstanceType`` and ``NetworkInterfaces``
    (single-AZ subnet) per-call. Empty ``InstanceMarketOptions``
    overrides the LT's MarketType=spot when ``on_demand=True``.

    Returns the new InstanceId on success, ``None`` if AWS refused
    with a spot- or capacity-related code (InsufficientInstanceCapacity,
    MaxSpotInstanceCountExceeded, etc). Other ClientErrors propagate
    so callers see real bugs (IAM, malformed template, etc).
    """
    kwargs: dict[str, Any] = {
        "LaunchTemplate": {
            "LaunchTemplateName": LAUNCH_TEMPLATE_NAME,
            "Version": "$Default",
        },
        "InstanceType": instance_type,
        "MinCount": 1,
        "MaxCount": 1,
        "NetworkInterfaces": [
            {
                "DeviceIndex": 0,
                "AssociatePublicIpAddress": True,
                "Groups": [WORKER_SECURITY_GROUP_ID],
                "SubnetId": subnet_id,
            }
        ],
    }
    if on_demand:
        kwargs["InstanceMarketOptions"] = {}

    try:
        resp = ec2.run_instances(**kwargs)
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code", "")
        if on_demand and code in _ONDEMAND_FALLBACK_CODES:
            return None  # try a different AZ or instance type
        if not on_demand and code in _SPOT_FAILURE_CODES:
            return None  # try a different AZ, market, or instance type
        raise
    instances = resp.get("Instances") or []
    if not instances:
        raise RuntimeError("RunInstances returned no Instances")
    return instances[0]["InstanceId"]


def _launch_one(ec2: Any) -> str:
    """Synchronous boto3 call: boot exactly one worker.

    Triple-tier fallback (T7n + T7q), with market preference control:
      * outer: instance-type ladder — try the largest GPU SKU first,
        fall back to smaller ones to absorb capacity drought
        (:data:`INSTANCE_TYPE_LADDER`).
      * middle: market — controlled by SAM_GPU_MARKET env var:
          - "ondemand" (default): on-demand only (no spot attempts)
          - "spot-first": spot first, on-demand fallback (legacy)
      * inner: AZ rotation — try each us-east-2 AZ in turn, since
        g6e capacity flits between AZs in real time (T7n / §40).

    Default (on-demand-only) order per instance type: on-demand
    2a/2b/2c (3 attempts), then step down. Full ladder = 12 attempts.

    Spot-first order per instance type: spot 2a/2b/2c → on-demand
    2a/2b/2c (6 attempts), then step down. Full ladder = 24 attempts.

    Each attempt is ~2-3 s so worst-case total wall is 36 s (on-demand)
    or ~1 min (spot-first). Non-capacity ClientErrors (IAM, malformed
    template) propagate immediately.

    Owner directive 2026-06-11: "8장부터 시작해서 실패하면 7장 이런식
    으로 내려와서 최대한 빨리 실행하게" — the ladder embodies that
    "biggest available wins" preference.

    Owner directive 2026-06-16: on-demand by default; spot reclamation
    (Server.SpotInstanceTermination) wastes compute. Cost delta ~21%
    (g6e.48xlarge $5.96 spot → $7.23 on-demand) is acceptable.
    """
    azs = list(SUBNETS_BY_AZ.keys())  # ['us-east-2a', '2b', '2c']
    market = _market_preference()

    for instance_type in INSTANCE_TYPE_LADDER:
        if market == "spot-first":
            # Legacy: Phase 1 spot, Phase 2 on-demand
            for az in azs:
                subnet = SUBNETS_BY_AZ[az]
                result = _try_run_instances(
                    ec2,
                    instance_type=instance_type,
                    subnet_id=subnet,
                    on_demand=False,
                )
                if result is not None:
                    logger.info(
                        "launched %s spot in %s (subnet %s) → %s",
                        instance_type, az, subnet, result,
                    )
                    return result
                logger.info(
                    "%s spot refused in %s — trying next AZ", instance_type, az,
                )

            logger.info(
                "%s spot exhausted; switching to on-demand", instance_type,
            )

        # On-demand phase (either exclusive or fallback depending on market)
        for az in azs:
            subnet = SUBNETS_BY_AZ[az]
            result = _try_run_instances(
                ec2,
                instance_type=instance_type,
                subnet_id=subnet,
                on_demand=True,
            )
            if result is not None:
                logger.info(
                    "launched %s on-demand in %s (subnet %s) → %s",
                    instance_type, az, subnet, result,
                )
                return result
            logger.info(
                "%s on-demand refused in %s — trying next AZ",
                instance_type, az,
            )

        markets_tried = "spot+on-demand" if market == "spot-first" else "on-demand"
        logger.info(
            "%s exhausted across all AZs (%s) — stepping down",
            instance_type, markets_tried,
        )

    # All instance types × AZs × markets refused.
    markets_desc = "both markets (spot+on-demand)" if market == "spot-first" else "on-demand"
    raise GpuCapacityUnavailable(
        "GPU capacity unavailable in us-east-2 across the full ladder "
        f"({list(INSTANCE_TYPE_LADDER)}) × all AZs (2a/2b/2c) × "
        f"{markets_desc}. Retry in a few minutes."
    )


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


async def ensure_worker_running(
    *,
    ec2_client_factory: Ec2ClientFactory | None = None,
    advisory_lock: AdvisoryLock,
) -> LaunchResult:
    """Ensure at least one GPU worker is alive; boot one if not.

    Args:
        ec2_client_factory: Optional override returning an EC2 client.
            Defaults to a regional boto3 client.
        advisory_lock: A lock object whose ``acquire()`` returns ``True``
            iff this call won the boot window. Production wires this to
            a PG ``pg_try_advisory_lock(ADVISORY_LOCK_KEY)``.

    Returns:
        :class:`LaunchResult` with ``action`` set to ``"launched"`` or
        ``"noop"``. ``"noop"`` carries a ``reason`` (one of
        ``"boot_window_locked"`` or ``"worker_already_running"``).

    Raises:
        :class:`GpuCapacityUnavailable`: if RunInstances failed with
            ``InsufficientInstanceCapacity``. Other ClientErrors
            propagate unchanged.
    """
    factory = ec2_client_factory or _default_ec2_client_factory

    got_lock = await advisory_lock.acquire()
    if not got_lock:
        # Another defer call is already inside the boot window. Trust
        # them to do (or skip) the launch — we don't even bother
        # describing.
        logger.debug("ensure_worker_running: boot window held, no-op")
        return LaunchResult(action="noop", reason="boot_window_locked")

    try:
        ec2 = factory()
        loop = asyncio.get_running_loop()

        # describe_instances is synchronous; offload to a thread so we
        # don't block the event loop on the round-trip.
        has_live = await loop.run_in_executor(None, _has_live_worker, ec2)
        if has_live:
            logger.info("ensure_worker_running: live worker exists, no-op")
            return LaunchResult(action="noop", reason="worker_already_running")

        instance_id = await loop.run_in_executor(None, _launch_one, ec2)
        logger.info(
            "ensure_worker_running: launched %s via template %s",
            instance_id,
            LAUNCH_TEMPLATE_NAME,
        )
        return LaunchResult(action="launched", instance_id=instance_id)
    finally:
        await advisory_lock.release()
