"""API-side spot launcher (P4.4).

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
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

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

#: Tag values used to identify production GPU workers (cost-allocation
#: tag is also ``Project=qpress-sam`` for budget filtering — see P4.5).
TAG_PROJECT: str = "qpress-sam"
TAG_ROLE_WORKER: str = "worker"

#: PG advisory-lock key for the boot window. Shared by all defer
#: callers. Value is arbitrary but stable ('0xCAFE0044' = "P4.4 boot
#: window"). The production caller passes this to
#: ``pg_try_advisory_lock(key)``.
ADVISORY_LOCK_KEY: int = 0xCAFE0044


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


def _launch_one(ec2: Any) -> str:
    """Synchronous boto3 call: boot exactly one spot worker.

    Translates ``InsufficientInstanceCapacity`` into
    :class:`GpuCapacityUnavailable`. All other ClientErrors propagate.
    """
    try:
        resp = ec2.run_instances(
            LaunchTemplate={
                "LaunchTemplateName": LAUNCH_TEMPLATE_NAME,
                "Version": "$Default",
            },
            MinCount=1,
            MaxCount=1,
        )
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code", "")
        if code == "InsufficientInstanceCapacity":
            raise GpuCapacityUnavailable(
                "GPU spot capacity unavailable in us-east-2. Retry in a few minutes."
            ) from e
        raise

    instances = resp.get("Instances") or []
    if not instances:
        # Defensive: the API contract says Instances is non-empty on success.
        raise RuntimeError("RunInstances returned no Instances")
    return instances[0]["InstanceId"]


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
