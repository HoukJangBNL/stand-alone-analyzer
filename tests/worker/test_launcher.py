"""P4.4 — API-side spot launcher unit tests.

The launcher is the API-side seam that boots a GPU worker EC2 instance
on demand when there is no live worker draining the procrastinate
``gpu`` queue. It is invoked just before ``defer_async`` from the SAM
route(s).

Behaviors under test
--------------------
1. Worker already running → does NOT call run-instances.
2. No worker → calls run-instances exactly once with the right launch
   template name (``qpress-sam-gpu-worker``) at the default version.
3. Race protection: two concurrent ``ensure_worker_running()`` calls
   arriving within the same boot window result in **one** RunInstances
   call. The second caller acquires the same advisory lock, sees a
   worker already in pending state, and is a no-op.
4. ``InsufficientInstanceCapacity`` from boto3 → surfaces as a
   :class:`flake_analysis.worker.launcher.GpuCapacityUnavailable` error
   so the API can wire it through the existing pipeline_error envelope.

The launcher uses a synchronous boto3 client wrapped in an executor so
the API's event loop is not blocked. Tests inject a fake EC2 client
factory — no real boto3 client, no AWS calls.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from botocore.exceptions import ClientError


# ---------------------------------------------------------------------------
# Fake EC2 client
# ---------------------------------------------------------------------------


class FakeEc2Client:
    """In-memory stand-in for a boto3 EC2 client.

    Records describe-instances/run-instances calls and returns scripted
    responses. Mirrors only the slice of the boto3 surface the launcher
    actually touches — not a full mock of the EC2 API.
    """

    def __init__(self) -> None:
        self.describe_calls: list[dict[str, Any]] = []
        self.run_calls: list[dict[str, Any]] = []
        # Configurable response state
        self._existing_instances: list[dict[str, Any]] = []
        self._run_error: ClientError | None = None
        self._run_response: dict[str, Any] = {
            "Instances": [{"InstanceId": "i-fake12345"}]
        }

    # --- describe_instances ------------------------------------------------
    def describe_instances(self, **kwargs: Any) -> dict[str, Any]:
        self.describe_calls.append(kwargs)
        return {
            "Reservations": [
                {"Instances": self._existing_instances}
            ]
            if self._existing_instances
            else []
        }

    def set_existing_workers(self, *states: str) -> None:
        """Each arg is a state name like 'running', 'pending', 'terminated'."""
        self._existing_instances = [
            {
                "InstanceId": f"i-existing{i}",
                "State": {"Name": state},
                "Tags": [
                    {"Key": "Project", "Value": "qpress-sam"},
                    {"Key": "Role", "Value": "worker"},
                ],
            }
            for i, state in enumerate(states)
        ]

    # --- run_instances -----------------------------------------------------
    def run_instances(self, **kwargs: Any) -> dict[str, Any]:
        if self._run_error is not None:
            raise self._run_error
        self.run_calls.append(kwargs)
        return self._run_response

    def set_run_error(self, code: str, message: str) -> None:
        self._run_error = ClientError(
            {"Error": {"Code": code, "Message": message}},
            "RunInstances",
        )


# ---------------------------------------------------------------------------
# Fake advisory-lock helper
# ---------------------------------------------------------------------------


class FakeAdvisoryLock:
    """Stand-in for the PG advisory lock used to serialise boot windows.

    Tests don't have a real PG session here. The launcher resolves the
    lock through an injectable async callable; this fake records calls
    and lets the test simulate "lock already held by another caller".
    """

    def __init__(self) -> None:
        self.acquire_calls: int = 0
        self.release_calls: int = 0
        self._holder_count: int = 0

    async def acquire(self) -> bool:
        self.acquire_calls += 1
        if self._holder_count > 0:
            return False  # someone else holds the lock
        self._holder_count = 1
        return True

    async def release(self) -> None:
        self.release_calls += 1
        self._holder_count = max(0, self._holder_count - 1)

    def force_held(self) -> None:
        """Pretend a different connection already holds the lock."""
        self._holder_count = 1


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_running_worker_does_not_boot_new_instance():
    """If a tagged worker is already RUNNING, ensure_worker_running is a no-op."""
    from flake_analysis.worker import launcher

    ec2 = FakeEc2Client()
    ec2.set_existing_workers("running")
    lock = FakeAdvisoryLock()

    result = await launcher.ensure_worker_running(
        ec2_client_factory=lambda: ec2,
        advisory_lock=lock,
    )

    assert result.action == "noop"
    assert result.reason == "worker_already_running"
    # No boot attempted.
    assert ec2.run_calls == []
    # We still acquire/release the lock so the result is serialised.
    assert lock.acquire_calls == 1
    assert lock.release_calls == 1


@pytest.mark.asyncio
async def test_pending_worker_does_not_boot_new_instance():
    """A worker in 'pending' is mid-boot; don't launch another."""
    from flake_analysis.worker import launcher

    ec2 = FakeEc2Client()
    ec2.set_existing_workers("pending")
    lock = FakeAdvisoryLock()

    result = await launcher.ensure_worker_running(
        ec2_client_factory=lambda: ec2,
        advisory_lock=lock,
    )

    assert result.action == "noop"
    assert result.reason == "worker_already_running"
    assert ec2.run_calls == []


@pytest.mark.asyncio
async def test_no_worker_boots_one_via_launch_template():
    """Empty fleet → launcher calls run-instances with the launch template name."""
    from flake_analysis.worker import launcher

    ec2 = FakeEc2Client()
    # No existing workers.
    lock = FakeAdvisoryLock()

    result = await launcher.ensure_worker_running(
        ec2_client_factory=lambda: ec2,
        advisory_lock=lock,
    )

    assert result.action == "launched"
    assert result.instance_id == "i-fake12345"
    assert len(ec2.run_calls) == 1
    call = ec2.run_calls[0]
    # MinCount/MaxCount must be exactly 1 — never let a bug spawn many.
    assert call["MinCount"] == 1
    assert call["MaxCount"] == 1
    assert call["LaunchTemplate"] == {
        "LaunchTemplateName": "qpress-sam-gpu-worker",
        "Version": "$Default",
    }


@pytest.mark.asyncio
async def test_terminated_workers_are_ignored():
    """A 'terminated' worker doesn't count as running — boot a new one."""
    from flake_analysis.worker import launcher

    ec2 = FakeEc2Client()
    ec2.set_existing_workers("terminated", "shutting-down")
    lock = FakeAdvisoryLock()

    result = await launcher.ensure_worker_running(
        ec2_client_factory=lambda: ec2,
        advisory_lock=lock,
    )

    assert result.action == "launched"
    assert len(ec2.run_calls) == 1


@pytest.mark.asyncio
async def test_describe_instances_filters_on_project_and_role_tags():
    """The describe call must filter on Project=qpress-sam AND Role=worker."""
    from flake_analysis.worker import launcher

    ec2 = FakeEc2Client()
    lock = FakeAdvisoryLock()

    await launcher.ensure_worker_running(
        ec2_client_factory=lambda: ec2,
        advisory_lock=lock,
    )

    assert len(ec2.describe_calls) == 1
    filters = ec2.describe_calls[0]["Filters"]
    # Build a name->values map for assertion.
    name_to_values = {f["Name"]: set(f["Values"]) for f in filters}
    assert "qpress-sam" in name_to_values["tag:Project"]
    assert "worker" in name_to_values["tag:Role"]
    # State filter must include the live states only.
    assert {"pending", "running"}.issubset(name_to_values["instance-state-name"])


@pytest.mark.asyncio
async def test_lock_already_held_makes_call_a_noop():
    """If another caller holds the advisory lock, we skip the launch attempt."""
    from flake_analysis.worker import launcher

    ec2 = FakeEc2Client()
    lock = FakeAdvisoryLock()
    lock.force_held()  # someone else owns the boot window

    result = await launcher.ensure_worker_running(
        ec2_client_factory=lambda: ec2,
        advisory_lock=lock,
    )

    assert result.action == "noop"
    assert result.reason == "boot_window_locked"
    # No describe, no run.
    assert ec2.describe_calls == []
    assert ec2.run_calls == []
    # We still tried to acquire (and the FakeAdvisoryLock returned False);
    # we never call release because we never acquired.
    assert lock.acquire_calls == 1
    assert lock.release_calls == 0


@pytest.mark.asyncio
async def test_concurrent_calls_only_boot_once():
    """Two concurrent ensure_worker_running calls → exactly one RunInstances."""
    from flake_analysis.worker import launcher

    ec2 = FakeEc2Client()
    # Single shared lock state, but the two callers race for it. The first
    # acquires, runs the boot, and the second sees the lock held → noop.
    # We simulate that by having the first call hold the lock until both
    # have entered ensure_worker_running.

    class GatedLock(FakeAdvisoryLock):
        def __init__(self) -> None:
            super().__init__()
            self._gate = asyncio.Event()
            self._first_acquired = asyncio.Event()

        async def acquire(self) -> bool:
            self.acquire_calls += 1
            if self._holder_count > 0:
                return False
            self._holder_count = 1
            # First acquire: signal that we're in the critical section, then
            # wait for the test to release us.
            if self.acquire_calls == 1:
                self._first_acquired.set()
                await self._gate.wait()
            return True

        async def release(self) -> None:
            self.release_calls += 1
            self._holder_count = max(0, self._holder_count - 1)

    lock = GatedLock()

    async def attempt():
        return await launcher.ensure_worker_running(
            ec2_client_factory=lambda: ec2,
            advisory_lock=lock,
        )

    t1 = asyncio.create_task(attempt())
    # Wait until t1 has the lock and is suspended inside acquire().
    await lock._first_acquired.wait()
    t2 = asyncio.create_task(attempt())
    # Give t2 a chance to call acquire and bounce off the held lock.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    # Now release t1.
    lock._gate.set()
    r1, r2 = await asyncio.gather(t1, t2)

    actions = sorted([r1.action, r2.action])
    assert actions == ["launched", "noop"]
    # Exactly one boot happened.
    assert len(ec2.run_calls) == 1


@pytest.mark.asyncio
async def test_insufficient_capacity_raises_typed_error():
    """Spot capacity exhausted → GpuCapacityUnavailable, not raw ClientError."""
    from flake_analysis.worker import launcher

    ec2 = FakeEc2Client()
    ec2.set_run_error(
        "InsufficientInstanceCapacity",
        "We currently do not have sufficient g6e.xlarge capacity in the AZ.",
    )
    lock = FakeAdvisoryLock()

    with pytest.raises(launcher.GpuCapacityUnavailable) as exc_info:
        await launcher.ensure_worker_running(
            ec2_client_factory=lambda: ec2,
            advisory_lock=lock,
        )

    # Caller-friendly message — used in the toast.
    assert "capacity" in str(exc_info.value).lower()
    # Lock must be released even on error.
    assert lock.release_calls == 1


@pytest.mark.asyncio
async def test_other_client_errors_propagate():
    """Non-capacity ClientErrors (e.g. UnauthorizedOperation) bubble up as-is."""
    from flake_analysis.worker import launcher

    ec2 = FakeEc2Client()
    ec2.set_run_error("UnauthorizedOperation", "You are not authorized.")
    lock = FakeAdvisoryLock()

    with pytest.raises(ClientError):
        await launcher.ensure_worker_running(
            ec2_client_factory=lambda: ec2,
            advisory_lock=lock,
        )
    assert lock.release_calls == 1


@pytest.mark.asyncio
async def test_default_factory_uses_boto3_ec2_client(monkeypatch):
    """When no factory is injected, the launcher builds a real boto3 client.

    We don't actually call AWS — we patch boto3.client to return our fake.
    This guards against accidentally hard-coding the factory path during
    refactors.
    """
    import boto3

    from flake_analysis.worker import launcher

    captured: dict[str, Any] = {}
    fake_ec2 = FakeEc2Client()
    fake_ec2.set_existing_workers("running")  # keep this test minimal — noop path

    def fake_boto3_client(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return fake_ec2

    monkeypatch.setattr(boto3, "client", fake_boto3_client)

    lock = FakeAdvisoryLock()
    result = await launcher.ensure_worker_running(advisory_lock=lock)

    assert result.action == "noop"
    assert captured["name"] == "ec2"
    # us-east-2 is hard-coded as that's where qpress-sam-gpu-worker lives.
    assert captured["kwargs"].get("region_name") == "us-east-2"
